"""Run original multi-OPF fixtures through the real Files page in a browser."""
import argparse
import base64
import hashlib
import json
from pathlib import Path
import sys
from urllib.parse import urlparse

from playwright.sync_api import sync_playwright


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, default=Path.cwd())
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--browser", help="Optional installed browser executable")
    parser.add_argument("--expect", choices=("working", "broken"), default="working")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    html = (args.source / "src/network/html/FilesPage.html").read_text()
    js = (args.source / "src/network/html/js/jszip.min.js").read_text()
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, **({"executable_path": args.browser} if args.browser else {}))
        page = browser.new_page(viewport={"width": 1200, "height": 900})

        def route(request):
            path = urlparse(request.request.url).path
            if path == "/":
                request.fulfill(body=html, content_type="text/html")
            elif path == "/js/jszip.min.js":
                request.fulfill(body=js, content_type="application/javascript")
            elif path == "/api/files":
                request.fulfill(json={"files": [], "path": "/"})
            elif path == "/api/status":
                request.fulfill(json={"device": "X4", "width": 480, "height": 800})
            else:
                request.abort()

        page.route("**/*", route)
        page.goto("http://127.0.0.1:8080/")
        page.wait_for_function("typeof convertEpubFile === 'function' && typeof JSZip === 'function'")
        results = page.evaluate('''async () => {
          const results = [];
          const toBase64 = buffer => btoa(String.fromCharCode(...new Uint8Array(buffer)));
          for (const order of [['', '1/', '2/'], ['2/', '1/', ''], ['1/', '', '2/'], ['']]) {
            const zip = new JSZip();
            zip.file('mimetype', 'application/epub+zip', {compression: 'STORE', createFolders: false});
            zip.file('META-INF/container.xml', '<?xml version="1.0"?><container xmlns="urn:oasis:names:tc:opendocument:xmlns:container" version="1.0"><rootfiles><rootfile full-path="content.opf" media-type="application/oebps-package+xml"/></rootfiles></container>');
            for (const prefix of order) {
              const id = 'book-' + (prefix || 'root');
              zip.file(prefix + 'content.opf', '<?xml version="1.0"?><package xmlns="http://www.idpf.org/2007/opf" version="2.0" unique-identifier="id"><metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:identifier id="id">' + id + '</dc:identifier><dc:title>Original regression text</dc:title><dc:language>en</dc:language></metadata><manifest><item id="chapter" href="chapter.xhtml" media-type="application/xhtml+xml"/><item id="image" href="image.png" media-type="image/png"/><item id="font" href="font.bin" media-type="font/ttf"/><item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/></manifest><spine toc="ncx"><itemref idref="chapter"/></spine></package>');
              zip.file(prefix + 'chapter.xhtml', '<?xml version="1.0"?><html xmlns="http://www.w3.org/1999/xhtml"><head><title>Test</title></head><body><p>Original multi-package regression text.</p><img src="image.png" alt="original square"/></body></html>');
              zip.file(prefix + 'toc.ncx', '<?xml version="1.0"?><ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1"><head><meta name="dtb:uid" content="' + (order.length === 1 ? 'stale-single-id' : id) + '"/></head><docTitle><text>Test</text></docTitle><navMap><navPoint id="chapter" playOrder="1"><navLabel><text>Chapter</text></navLabel><content src="chapter.xhtml"/></navPoint></navMap></ncx>');
              zip.file(prefix + 'font.bin', new Uint8Array([0, 1, 2, 3]));
              const canvas = document.createElement('canvas'); canvas.width = 16; canvas.height = 16;
              const ctx = canvas.getContext('2d'); ctx.fillStyle = 'black'; ctx.fillRect(0, 0, 16, 16);
              const png = await new Promise(resolve => canvas.toBlob(resolve, 'image/png'));
              zip.file(prefix + 'image.png', await png.arrayBuffer());
            }
            zip.forEach((path, entry) => { entry.date = new Date('2020-01-01T00:00:00Z'); });
            const input = await zip.generateAsync({type: 'arraybuffer'});
            const blob = await convertEpubFile(new File([input], 'multi-opf.epub', {type: 'application/epub+zip'}));
            const output = await blob.arrayBuffer();
            const converted = await JSZip.loadAsync(output);
            const errors = [];
            for (const prefix of order) {
              const opf = converted.file(prefix + 'content.opf');
              if (!opf) { errors.push('missing OPF: ' + prefix + 'content.opf'); continue; }
              const doc = new DOMParser().parseFromString(await opf.async('string'), 'application/xml');
              if (doc.querySelector('parsererror')) errors.push('invalid OPF: ' + prefix);
              const items = [...doc.getElementsByTagNameNS('*', 'item')];
              const image = items.find(i => i.getAttribute('id') === 'image');
              if (!image || image.getAttribute('href') !== 'image.jpg' || image.getAttribute('media-type') !== 'image/jpeg') errors.push('image manifest not updated: ' + prefix);
              if (items.some(i => i.getAttribute('id') === 'font') || converted.file(prefix + 'font.bin')) errors.push('font not removed: ' + prefix);
              for (const item of items) {
                if (!converted.file(prefix + item.getAttribute('href'))) errors.push('dangling manifest: ' + prefix + item.getAttribute('href'));
              }
              const ncx = new DOMParser().parseFromString(await converted.file(prefix + 'toc.ncx').async('string'), 'application/xml');
              const uid = [...ncx.getElementsByTagNameNS('*', 'meta')].find(m => m.getAttribute('name') === 'dtb:uid');
              if (uid.getAttribute('content') !== 'book-' + (prefix || 'root')) errors.push('wrong NCX identifier: ' + prefix);
            }
            const container = new DOMParser().parseFromString(await converted.file('META-INF/container.xml').async('string'), 'application/xml');
            const rootfile = container.getElementsByTagNameNS('*', 'rootfile')[0].getAttribute('full-path');
            if (!converted.file(rootfile)) errors.push('missing container target: ' + rootfile);
            results.push({order, errors, opfs: Object.keys(converted.files).filter(p => p.endsWith('.opf')), input: toBase64(input), output: toBase64(output)});
          }
          return results;
        }''')
        browser.close()
    for index, result in enumerate(results):
        for label in ("input", "output"):
            data = base64.b64decode(result.pop(label))
            (args.output / f"case-{index}-{label}.epub").write_bytes(data)
            result[label + "_sha256"] = hashlib.sha256(data).hexdigest()
    (args.output / "result.json").write_text(json.dumps(results, indent=2) + "\n")
    print(json.dumps(results, indent=2))
    if args.expect == "broken":
        expected = [
            ["missing OPF: content.opf", "missing OPF: 1/content.opf", "missing container target: content.opf"],
            ["missing OPF: 2/content.opf", "missing OPF: 1/content.opf"],
            ["missing OPF: 1/content.opf", "missing OPF: content.opf", "missing container target: content.opf"],
            [],
        ]
        return 0 if [result["errors"] for result in results] == expected else 1
    return 1 if any(result["errors"] for result in results) else 0


if __name__ == "__main__":
    sys.exit(main())
