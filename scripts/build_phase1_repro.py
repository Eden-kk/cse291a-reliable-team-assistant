"""Package an immutable formal archive with portable offline verification."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import zipfile
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
from pypdf import PdfReader, PdfWriter

ROOT = Path(__file__).resolve().parents[1]


def build(source, vendor):
    out = ROOT / 'output/phase1-2026-10-24-repro'
    out.mkdir(parents=True, exist_ok=False)
    stage = out / 'package'
    with zipfile.ZipFile(source / 'Phase1-Evidence.zip') as archive:
        archive.extractall(stage)
    for name in ('scripts/verify_formal_release.py', 'docs/phase1-reproduction-20261024.txt'):
        target = stage / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / name, target)
    shutil.copytree(ROOT / 'tests', stage / 'tests', ignore=shutil.ignore_patterns('__pycache__'))
    shutil.copy2(ROOT / 'docs/phase1-reproduction-20261024.txt', stage / 'README-REPRODUCTION.txt')
    for name in ('agents/cloudops_agent', 'LICENSE', 'process-label/boutique/runtime/32',
                 'process-label/boutique/service/12', 'process-label/boutique/performance/5'):
        target = stage / 'vendor_offline/Cloud-OpsBench' / name
        target.parent.mkdir(parents=True, exist_ok=True)
        if (vendor / name).is_dir():
            shutil.copytree(vendor / name, target, ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
        else:
            shutil.copy2(vendor / name, target)
    styles = getSampleStyleSheet()
    flow = [Paragraph('Phase 1: Reproduction and Timeline Appendix', styles['Title'])]
    for title, text in [
        ('Reproducible artifact', 'The accompanying ZIP includes pinned native evaluator source, MIT license, three scored label sets, code, tests and a portable offline verifier. README-REPRODUCTION.txt gives clean extraction commands and the pinned source acquisition command. Original formal review judgments and their seal are unchanged. The verifier resolves the review by sealed content hash, not the author-specific absolute path.'),
        ('Measurement boundaries', 'The stored seconds field sums API-call durations; integrated end-to-end latency and formal tracking overhead were not recorded. No numeric replacement is invented. Four eligible injections did not cause adoption of the false port; two units were ineligible. Equal 3/4 recovery success counts have different failed cases and do not establish quality equivalence.'),
        ('Completed October 24', 'Formal scoring, billing reconciliation and immutable evidence preservation. Portable saved-output verification is separate from fresh inference and cannot establish repeatability of a mutable model alias. Input-only judgments were produced by Codex, not independent human review.'),
        ('October 24-28', 'Finish clean extraction checks and causal-error development on seen cases; keep failures and ignored injections. Prepare the Phase 1 submission by the saved October 28 course target; confirm exact current time and upload slots before submission. Nothing in this release has been submitted.'),
        ('Next experimental milestone', 'Freeze a new causal fault protocol only after development establishes propagation. Compare no recovery, restart and selective recovery from the same failure state. Include shared/complete invalidation and missing provenance. Newly selected evaluation cases must be separate from all exposed cases.'),
        ('Remaining term', 'Analyze paired quality before cost; add scheduling or learned-memory extensions only after the core recovery claim is supported. Recheck API expiry and budget before any additional online batch. Final artifact/report preparation targets late November to December; verify official deadlines. Benchmark acceptance remains unverified.'),
    ]:
        flow.extend([Paragraph(title, styles['Heading2']), Paragraph(text, styles['BodyText']), Spacer(1, 8)])
    appendix = out / 'appendix.pdf'
    SimpleDocTemplate(str(appendix), leftMargin=54, rightMargin=54, topMargin=40, bottomMargin=40).build(flow)
    writer = PdfWriter()
    for file in (source / 'Phase1-Report.pdf', appendix):
        for page in PdfReader(file).pages:
            writer.add_page(page)
    with (out / 'Phase1-Report.pdf').open('wb') as f:
        writer.write(f)
    shutil.copy2(out / 'Phase1-Report.pdf', stage / 'Phase1-Report.pdf')
    shutil.copy2(source / 'Phase1-Report.txt', out / 'Phase1-Report.txt')
    with (out / 'Phase1-Report.txt').open('a') as f:
        f.write('\n\n' + (ROOT / 'docs/phase1-reproduction-20261024.txt').read_text())
    shutil.copy2(out / 'Phase1-Report.txt', stage / 'Phase1-Report.txt')
    files = {str(p.relative_to(stage)): hashlib.sha256(p.read_bytes()).hexdigest()
             for p in sorted(stage.rglob('*')) if p.is_file()}
    (stage / 'RELEASE-MANIFEST.json').write_text(json.dumps({'dataset_commit': 'ea05dafbc87b5985df8721622d63d4229c2b4224', 'files': files}, indent=2))
    with zipfile.ZipFile(out / 'Phase1-Evidence.zip', 'w', zipfile.ZIP_DEFLATED) as z:
        for p in sorted(stage.rglob('*')):
            if p.is_file(): z.write(p, p.relative_to(stage))
    print(out)


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--source', required=True, type=Path)
    p.add_argument('--vendor', required=True, type=Path)
    args = p.parse_args()
    build(args.source, args.vendor)
