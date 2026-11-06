#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""iso-pick: 从远程 ISO/UDF 镜像中只取需要的几个文件，不下载整包。

原理：
  1. 服务器支持 HTTP Range（字节范围请求）时，把远端大文件当作可随机读的本地文件；
  2. 用 pycdlib 解析 ISO9660/UDF 目录索引，拿到目标文件的精确偏移与长度；
  3. 只对这几个区间发 Range 请求，多路并行写盘，带断点续传与校验。

用途不限于 Windows 语言包：任何「索引可随机访问、服务器支持 Range」的大镜像
（ZIP/WIM/VHD 等需另接解析器；本工具当前实现 ISO9660 + UDF）。

用法：
  python iso_pick.py list  <URL> [--root PATH] [--filter GLOB] [--json OUT]
  python iso_pick.py fetch <URL> --pattern GLOB [--pattern GLOB ...] --out DIR
  python iso_pick.py repo  <URL> --language zh-HK [--fonts Hant] --out DIR
  python iso_pick.py repo  --config examples/zh-hk-23h2.toml

通用选项：
  --proxy URL      代理地址（默认直连；也可设环境变量 ISO_PICK_PROXY）
  --cache-dir DIR  元数据块缓存目录（重复解析可零网络请求）
  --workers N      并行连接数，默认 8
  --timeout SEC    单次请求超时，默认 60

依赖：Python 3.11+（用到 tomllib）、pycdlib（pip install pycdlib）
"""

import argparse
import concurrent.futures
import fnmatch
import hashlib
import http.client
import json
import os
import ssl
import sys
import threading
import time
import urllib.parse

try:
    import pycdlib
except ImportError:
    sys.exit('缺少依赖 pycdlib，请先运行：python -m pip install pycdlib')

BLOCK = 256 * 1024        # 元数据随机读的块大小
CHUNK = 2 * 1024 * 1024   # 目标文件并行下载的分片大小
DEFAULT_ROOT = '/LanguagesAndOptionalFeatures'


def log(msg):
    print(msg, flush=True)


# --------------------------------------------------------------------------
# 远端随机读
# --------------------------------------------------------------------------
class RangeReader:
    """只读、可 seek 的 file-like 对象，底层是 HTTP Range 请求。"""

    def __init__(self, url, proxy=None, cache_dir=None, timeout=60, retries=4):
        u = urllib.parse.urlparse(url)
        if u.scheme not in ('http', 'https') or not u.hostname:
            raise ValueError('URL 必须是完整的 http(s) 地址')
        self.host = u.hostname
        self.port = u.port or (443 if u.scheme == 'https' else 80)
        self.path = u.path or '/'
        self.scheme = u.scheme
        self.url = url
        self.proxy = proxy or os.environ.get('ISO_PICK_PROXY')
        self.timeout = timeout
        self.retries = retries
        self.requests = 0
        self.bytes_read = 0
        self.cache = {}
        self.cache_dir = cache_dir
        self.pos = 0
        self.conn = None
        if self.cache_dir:
            os.makedirs(self.cache_dir, exist_ok=True)
        self._connect()
        self.total, self.etag, self.accept_ranges = self._head()

    # -- 连接管理 ----------------------------------------------------------
    def _connect(self):
        if self.conn:
            try:
                self.conn.close()
            except Exception:
                pass
        last = None
        for attempt in range(self.retries):
            try:
                ctx = ssl.create_default_context() if self.scheme == 'https' else None
                if self.proxy:
                    p = urllib.parse.urlparse(
                        self.proxy if '://' in self.proxy else 'http://' + self.proxy)
                    conn = http.client.HTTPSConnection(p.hostname, p.port or 80,
                                                       context=ctx, timeout=self.timeout)
                    conn.set_tunnel(self.host, self.port)
                elif self.scheme == 'https':
                    conn = http.client.HTTPSConnection(self.host, self.port,
                                                       context=ctx, timeout=self.timeout)
                else:
                    conn = http.client.HTTPConnection(self.host, self.port,
                                                      timeout=self.timeout)
                conn.connect()
                self.conn = conn
                return
            except Exception as ex:
                last = ex
                print('  连接重试 %d/%d: %s' % (attempt + 1, self.retries, ex),
                      file=sys.stderr, flush=True)
                time.sleep(0.5)
        raise RuntimeError('无法连接 %s：%s' % (self.host, last))

    def _head(self):
        for attempt in range(self.retries):
            try:
                self.conn.request('HEAD', self.path,
                                  headers={'Host': self.host,
                                           'Accept-Encoding': 'identity'})
                r = self.conn.getresponse()
                r.read()
                if r.status not in (200, 206):
                    raise RuntimeError('HEAD 返回 %s' % r.status)
                total = int(r.getheader('Content-Length', '0'))
                etag = r.getheader('ETag') or r.getheader('Last-Modified') or ''
                ar = r.getheader('Accept-Ranges', '')
                if total <= 0:
                    raise RuntimeError('HEAD 未返回 Content-Length')
                if 'bytes' not in ar.lower():
                    print('  警告：服务器未声明 Accept-Ranges: bytes，Range 可能被忽略',
                          file=sys.stderr, flush=True)
                return total, etag, ar
            except Exception as ex:
                print('  HEAD 重试 %d/%d: %s' % (attempt + 1, self.retries, ex),
                      file=sys.stderr, flush=True)
                self._connect()
                time.sleep(0.5)
        raise RuntimeError('HEAD 失败，放弃')

    # -- 单块读取 ----------------------------------------------------------
    def fetch(self, start, length):
        end = min(start + length, self.total) - 1
        want = end - start + 1
        base = {'Host': self.host,
                'Range': 'bytes=%d-%d' % (start, end),
                'Accept-Encoding': 'identity',
                'Connection': 'keep-alive'}
        for attempt in range(self.retries):
            try:
                headers = dict(base)
                # If-Range 只接受强验证器；弱 ETag（W/"..."）会让服务器忽略 Range
                # 并返回 200 全文件，这里直接跳过，避免误收 6 GiB。
                if self.etag and not self.etag.startswith('W/'):
                    headers['If-Range'] = self.etag
                self.conn.request('GET', self.path, headers=headers)
                r = self.conn.getresponse()
                if r.status == 200:
                    r.close()
                    self._connect()
                    if 'If-Range' in headers:
                        self.etag = ''
                        raise RuntimeError('服务器忽略 Range（If-Range 未匹配），已清除验证器重试')
                    raise RuntimeError('服务器忽略 Range，返回 200 全文件')
                data = r.read()
                if r.status != 206:
                    raise RuntimeError('期望 206 Partial Content，实得 %s' % r.status)
                if len(data) != want:
                    raise RuntimeError('长度不符：%d / %d' % (len(data), want))
                self.requests += 1
                self.bytes_read += len(data)
                return data
            except Exception as ex:
                print('  Range 重试 %d/%d @%d: %s' % (attempt + 1, self.retries,
                                                    start, ex),
                      file=sys.stderr, flush=True)
                self._connect()
                time.sleep(0.5)
        raise RuntimeError('Range 读取失败 @%d' % start)

    def _block(self, start, length):
        cf = None
        if self.cache_dir:
            cf = os.path.join(self.cache_dir, '%d_%d.bin' % (start, length))
            if os.path.exists(cf):
                with open(cf, 'rb') as f:
                    buf = f.read()
                if len(buf) == length:
                    return buf
        buf = self.fetch(start, length)
        if cf and len(buf) == length:
            with open(cf, 'wb') as f:
                f.write(buf)
        return buf

    # -- file-like 接口 ----------------------------------------------------
    def seekable(self):
        return True

    def readable(self):
        return True

    def writable(self):
        return False

    def tell(self):
        return self.pos

    def seek(self, offset, whence=0):
        if whence == 0:
            self.pos = offset
        elif whence == 1:
            self.pos += offset
        elif whence == 2:
            self.pos = self.total + offset
        else:
            raise ValueError('whence 只能是 0/1/2')
        return self.pos

    def read(self, size=-1):
        if size is None or size < 0:
            size = self.total - self.pos
        if size > 64 * 1024 * 1024:
            raise RuntimeError('拒绝 %d 字节的巨型读取' % size)
        out = []
        while size > 0 and self.pos < self.total:
            hit = None
            for start, buf in self.cache.items():
                if start <= self.pos < start + len(buf):
                    hit = (start, buf)
                    break
            if hit is None:
                start = (self.pos // BLOCK) * BLOCK
                buf = self._block(start, min(BLOCK, self.total - start))
                self.cache[start] = buf
                if len(self.cache) > 32:
                    self.cache.pop(next(iter(self.cache)))
                hit = (start, buf)
            start, buf = hit
            chunk = buf[self.pos - start:self.pos - start + size]
            if not chunk:
                break
            out.append(chunk)
            self.pos += len(chunk)
            size -= len(chunk)
        return b''.join(out)


# --------------------------------------------------------------------------
# UDF 目录解析
# --------------------------------------------------------------------------
def decode_name(entry):
    """UDF 文件标识符是原始字节，须按 FID 记录的编码解码。"""
    fi = entry.file_identifier()
    if fi in (b'/', b''):
        return '/'
    enc = getattr(getattr(entry, 'file_ident', None), 'encoding', None)
    for codec in (enc, 'utf-16_be', 'latin-1'):
        if not codec:
            continue
        try:
            return fi.decode(codec)
        except (UnicodeDecodeError, LookupError):
            continue
    return fi.decode('utf-8', 'replace')


def iter_files(iso, root=DEFAULT_ROOT, max_depth=3):
    """产出 (相对路径, 绝对字节偏移, 长度)。"""
    def walk(path, depth):
        try:
            children = list(iso.list_children(udf_path=path))
        except Exception as ex:
            print('  跳过目录 %s：%s' % (path, ex), file=sys.stderr, flush=True)
            return
        for e in children:
            try:
                name = decode_name(e)
            except Exception:
                continue
            if name in ('.', '..', '/'):
                continue
            child = path.rstrip('/') + '/' + name
            if e.is_dir():
                if depth < max_depth:
                    walk(child, depth + 1)
            else:
                try:
                    off = e.inode.fp_offset
                    size = e.inode.get_data_length()
                except Exception:
                    continue
                yield child[len(root.rstrip('/')) + 1:], off, size
    yield from walk(root, 0)


def open_iso(reader):
    iso = pycdlib.PyCdlib()
    iso.open_fp(reader)
    return iso


# --------------------------------------------------------------------------
# 并行下载
# --------------------------------------------------------------------------
_tls = threading.local()


def _make_fetcher(spec, timeout, retries):
    """每个工作线程持有自己的 RangeReader（独立连接）。"""
    def get(start, length):
        r = getattr(_tls, 'reader', None)
        if r is None or r.url != spec['url']:
            r = RangeReader(spec['url'], proxy=spec['proxy'], timeout=timeout,
                            retries=retries)
            _tls.reader = r
        return r.fetch(start, length)
    return get


def download_files(spec, items, outdir, workers=8, timeout=60, retries=4,
                   expected_sha=None):
    """items: [(相对路径, 偏移, 长度)]，保持 ISO 内目录结构。"""
    fetch = _make_fetcher(spec, timeout, retries)
    summary = []
    for rel, off, size in items:
        dest = os.path.join(outdir, rel.replace('/', os.sep))
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        prog = dest + '.progress'
        done = set()
        if os.path.exists(prog) and os.path.exists(dest) and os.path.getsize(dest) == size:
            with open(prog) as f:
                done = set(json.load(f))
        if os.path.exists(dest) and os.path.getsize(dest) == size and not os.path.exists(prog):
            log('  已完整：%s' % rel)
        else:
            if not (os.path.exists(dest) and os.path.getsize(dest) == size):
                with open(dest, 'wb') as f:
                    f.truncate(size)
            nchunks = (size + CHUNK - 1) // CHUNK
            todo = [k for k in range(nchunks) if k not in done]
            log('  下载 %-70s %d 字节 / %d 片' % (rel[:70], size, len(todo)))
            ranges = [(off + k * CHUNK, min(CHUNK, size - k * CHUNK)) for k in todo]
            with open(dest, 'r+b') as f:
                with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
                    futs = {ex.submit(fetch, s, n): k
                            for k, (s, n) in zip(todo, ranges)}
                    for fut in concurrent.futures.as_completed(futs):
                        start, data = fut.result()
                        f.seek(start - off)
                        f.write(data)
                        f.flush()
                        done.add((start - off) // CHUNK)
                        with open(prog + '.tmp', 'w') as pf:
                            json.dump(sorted(done), pf)
                        os.replace(prog + '.tmp', prog)
        with open(dest, 'rb') as f:
            head = f.read(4)
            f.seek(0)
            sha = hashlib.sha256()
            for block in iter(lambda: f.read(1 << 20), b''):
                sha.update(block)
        digest = sha.hexdigest()
        if os.path.getsize(dest) != size:
            raise RuntimeError('长度校验失败：%s' % rel)
        if rel.lower().endswith('.cab') and head != b'MSCF':
            raise RuntimeError('CAB 头校验失败：%s' % rel)
        if expected_sha and expected_sha.get(rel) and expected_sha[rel] != digest:
            raise RuntimeError('SHA256 校验失败：%s' % rel)
        if os.path.exists(prog):
            os.remove(prog)
        summary.append((rel, size, digest))
        log('  OK %s  sha256=%s' % (rel, digest[:16]))
    return summary


# --------------------------------------------------------------------------
# 子命令
# --------------------------------------------------------------------------
def cmd_list(args, spec):
    reader = RangeReader(spec['url'], proxy=spec['proxy'],
                         cache_dir=spec['cache_dir'], timeout=args.timeout)
    iso = open_iso(reader)
    rows = []
    for rel, off, size in iter_files(iso, args.root):
        if args.filter and not fnmatch.fnmatch(rel.lower(), args.filter.lower()):
            continue
        rows.append({'path': rel, 'offset': off, 'size': size})
    for r in rows:
        log('%12d  @%12d  %s' % (r['size'], r['offset'], r['path']))
    log('共 %d 个文件；元数据网络请求 %d 次 / %.1f MiB' % (
        len(rows), reader.requests, reader.bytes_read / 1048576.0))
    if args.json:
        with open(args.json, 'w', encoding='utf-8') as f:
            json.dump({'source': spec['url'], 'etag': reader.etag,
                       'root': args.root, 'files': rows}, f,
                      ensure_ascii=False, indent=2)
        log('清单已写入 %s' % args.json)


def cmd_fetch(args, spec):
    reader = RangeReader(spec['url'], proxy=spec['proxy'],
                         cache_dir=spec['cache_dir'], timeout=args.timeout)
    iso = open_iso(reader)
    all_rows = [(rel, off, size) for rel, off, size in iter_files(iso, args.root)]
    picked = [r for r in all_rows
              if any(fnmatch.fnmatch(r[0].lower(), p.lower()) or
                     fnmatch.fnmatch(os.path.basename(r[0]).lower(), p.lower())
                     for p in args.pattern)]
    if not picked:
        sys.exit('没有匹配任何文件，请用 list --filter 确认模式')
    log('选中 %d 个文件，共 %.1f MiB（镜像总大小 %.2f GiB）' % (
        len(picked), sum(r[2] for r in picked) / 1048576.0,
        reader.total / 1073741824.0))
    download_files(spec, picked, args.out, workers=args.workers,
                   timeout=args.timeout)
    log('完成，输出目录：%s' % args.out)


def cmd_repo(args, spec):
    """组装一个可被 DISM /Source 直接使用的 FOD 仓库。"""
    patterns = [
        'FoDMetadata_Client.cab',
        'metadata/*.xml.cab',
        'Microsoft-Windows-LanguageFeatures-Basic-%s-Package~*.cab' % args.language,
    ]
    if args.fonts:
        patterns.append('Microsoft-Windows-LanguageFeatures-Fonts-%s-Package~*.cab'
                        % args.fonts)
    reader = RangeReader(spec['url'], proxy=spec['proxy'],
                         cache_dir=spec['cache_dir'], timeout=args.timeout)
    iso = open_iso(reader)
    all_rows = [(rel, off, size) for rel, off, size in iter_files(iso, args.root)]
    picked = [r for r in all_rows
              if any(fnmatch.fnmatch(r[0].lower(), p.lower()) or
                     fnmatch.fnmatch(os.path.basename(r[0]).lower(), p.lower())
                     for p in patterns)]
    if not picked:
        sys.exit('没有匹配任何文件，请检查 --language / --fonts / --root')
    # 完整性下限：FOD 仓库必须有主元数据 CAB 和 CompDB 目录
    names = [os.path.basename(r[0]).lower() for r in picked]
    if 'fodmetadata_client.cab' not in names:
        sys.exit('缺少 FoDMetadata_Client.cab，仓库不完整')
    compdb = [n for n in names if n.startswith('desktopTargetCompDB'.lower())]
    if not compdb:
        sys.exit('缺少 metadata/ 下的 CompDB 元数据，仓库不完整')
    total = sum(r[2] for r in picked)
    log('语言 %s：选中 %d 个文件（其中 CompDB %d 个），共 %.1f MiB' % (
        args.language, len(picked), len(compdb), total / 1048576.0))
    log('镜像总大小 %.2f GiB，本次仅取 %.2f%%' % (
        reader.total / 1073741824.0, total * 100.0 / reader.total))
    download_files(spec, picked, args.out, workers=args.workers,
                   timeout=args.timeout)
    log('仓库已就绪：%s' % args.out)
    log('下一步（管理员 PowerShell）：')
    log('  .\\tools\\install_fod.ps1 -Repo "%s" -Capability "Language.Basic~~~%s~0.0.1.0"'
        % (args.out, args.language))
    if args.fonts:
        log('  .\\tools\\install_fod.ps1 -Repo "%s" -Capability "Language.Fonts.%s~~~und-%s~0.0.1.0"'
            % (args.out, args.fonts, args.fonts))


def load_config(path):
    try:
        import tomllib
    except ImportError:
        sys.exit('需要 Python 3.11+ 才能读取 TOML 配置')
    with open(path, 'rb') as f:
        return tomllib.load(f)


def build_parser():
    p = argparse.ArgumentParser(
        description='从远程 ISO/UDF 镜像中只取需要的文件，不下载整包')
    p.add_argument('--proxy', help='代理地址；默认直连。也可设 ISO_PICK_PROXY')
    p.add_argument('--cache-dir', help='元数据块缓存目录')
    p.add_argument('--workers', type=int, default=8, help='并行连接数，默认 8')
    p.add_argument('--timeout', type=int, default=60, help='单次请求超时秒数')
    sub = p.add_subparsers(dest='command', required=True)

    pl = sub.add_parser('list', help='列出镜像内的文件与偏移')
    pl.add_argument('url', nargs='?', help='ISO 的 URL')
    pl.add_argument('--config', help='TOML 配置文件')
    pl.add_argument('--root', default=DEFAULT_ROOT)
    pl.add_argument('--filter', help='glob 过滤，如 "*Basic-zh-hk*"')
    pl.add_argument('--json', help='把清单写成 JSON')

    pf = sub.add_parser('fetch', help='按 glob 模式下载指定文件')
    pf.add_argument('url', nargs='?', help='ISO 的 URL')
    pf.add_argument('--config', help='TOML 配置文件')
    pf.add_argument('--root', default=DEFAULT_ROOT)
    pf.add_argument('--pattern', action='append', default=[], help='可重复')
    pf.add_argument('--out', required=True)

    pr = sub.add_parser('repo', help='组装 FOD 离线仓库')
    pr.add_argument('url', nargs='?', help='ISO 的 URL')
    pr.add_argument('--config', help='TOML 配置文件')
    pr.add_argument('--root', default=DEFAULT_ROOT)
    pr.add_argument('--language', default='zh-HK')
    pr.add_argument('--fonts', help='字型组名，如 Hant')
    pr.add_argument('--out', required=True)
    return p


def main():
    args = build_parser().parse_args()
    cfg = load_config(args.config) if args.config else {}
    iso_cfg = cfg.get('iso', {})
    url = args.url or iso_cfg.get('url')
    if not url:
        sys.exit('请提供 ISO 的 URL，或用 --config 指定配置文件')
    if args.command == 'fetch' and not args.pattern:
        args.pattern = cfg.get('patterns', [])
    if getattr(args, 'root', None) == DEFAULT_ROOT and iso_cfg.get('root'):
        args.root = iso_cfg['root']
    if args.command == 'repo':
        if 'case' in cfg:
            args.language = cfg['case'].get('language', args.language)
            args.fonts = cfg['case'].get('fonts', args.fonts)
    spec = {'url': url,
            'proxy': args.proxy or iso_cfg.get('proxy'),
            'cache_dir': args.cache_dir or iso_cfg.get('cache_dir')}
    {'list': cmd_list, 'fetch': cmd_fetch, 'repo': cmd_repo}[args.command](args, spec)


if __name__ == '__main__':
    main()
