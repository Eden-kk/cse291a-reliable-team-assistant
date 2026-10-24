"""Refresh a portable Phase 1 release from explicit, reviewed report content."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import zipfile
from xml.sax.saxutils import escape

from pypdf import PdfReader, PdfWriter
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

ROOT = Path(__file__).resolve().parents[1]


def hashes(directory):
    return {str(p.relative_to(directory)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(directory.rglob('*')) if p.is_file()}


def render_page(path, page):
    styles = getSampleStyleSheet()
    flow = [Paragraph(escape(page['title']), styles['Title'])]
    for section in page['sections']:
        if section.get('heading'):
            flow.append(Paragraph(escape(section['heading']), styles['Heading2']))
        flow.extend([Paragraph(escape(section['text']), styles['BodyText']), Spacer(1, 7)])
    SimpleDocTemplate(str(path), pagesize=(612, 792), leftMargin=54,
                      rightMargin=54, topMargin=38, bottomMargin=38).build(flow)
    if len(PdfReader(path).pages) != 1:
        raise ValueError(f'{path.name} must fit one page; edit report content before release')


def build(args):
    content = json.loads(args.content.read_text())
    if len(content['body']) != 2 or len(content['appendix']) != 1:
        raise ValueError('Require exactly two body pages and one timeline appendix')
    for path in (args.development, args.review):
        if not path.is_dir() or not any(path.rglob('*.json')):
            raise ValueError(f'Missing saved JSON evidence in {path}')
    for name in args.code:
        if not (ROOT / name).is_file():
            raise ValueError(f'Missing code: {name}')
    args.output.mkdir(parents=True, exist_ok=False)
    stage = args.output / 'package'
    with zipfile.ZipFile(args.source / 'Phase1-Evidence.zip') as archive:
        archive.extractall(stage)
    formal_before = hashes(stage / 'formal-results')
    if not formal_before:
        raise ValueError('Source release has no immutable formal evidence')
    for source, destination in ((args.development, 'development/handoff-dev-v2'),
                                (args.review, 'development/blinded-ai-review')):
        shutil.copytree(source, stage / destination,
                        ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    for name in args.code + ['scripts/build_phase1_handoff.py']:
        target = stage / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / name, target)
    shutil.copy2(args.content, stage / 'REPORT-CONTENT.json')
    writer = PdfWriter()
    text = []
    for index, page in enumerate(content['body'] + content['appendix'], 1):
        rendered = args.output / f'page-{index}.pdf'
        render_page(rendered, page)
        writer.add_page(PdfReader(rendered).pages[0])
        text.append(page['title'])
        for section in page['sections']:
            text.extend([section.get('heading', ''), section['text']])
    with (args.output / 'Phase1-Report.pdf').open('wb') as stream:
        writer.write(stream)
    (args.output / 'Phase1-Report.txt').write_text('\n\n'.join(t for t in text if t) + '\n')
    for name in ('Phase1-Report.pdf', 'Phase1-Report.txt'):
        shutil.copy2(args.output / name, stage / name)
    if hashes(stage / 'formal-results') != formal_before:
        raise ValueError('Formal archive changed during refresh')
    manifest_path = stage / 'RELEASE-MANIFEST.json'
    manifest = json.loads(manifest_path.read_text())
    manifest_path.unlink()
    manifest['files'] = hashes(stage)
    manifest_path.write_text(json.dumps(manifest, indent=2) + '\n')
    with zipfile.ZipFile(args.output / 'Phase1-Evidence.zip', 'w', zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(stage.rglob('*')):
            if path.is_file():
                archive.write(path, path.relative_to(stage))
    print(args.output)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--development', type=Path, required=True)
    parser.add_argument('--review', type=Path, required=True)
    parser.add_argument('--content', type=Path, required=True,
                        help='JSON with body[2] and appendix[1]; pages have title and sections')
    parser.add_argument('--code', nargs='+', required=True,
                        help='Repository-relative runner/verifier/config files to include')
    parser.add_argument('--output', type=Path,
                        default=ROOT / 'output/phase1-2026-10-24-handoff')
    build(parser.parse_args())
