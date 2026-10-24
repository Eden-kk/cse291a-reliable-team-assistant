"""Build a dated Phase 1 report and evidence release without replacing the Oct 8 archive."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import zipfile
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import SimpleDocTemplate, Paragraph, Table, TableStyle, PageBreak, Spacer
from reportlab.lib.pagesizes import letter

ROOT = Path(__file__).resolve().parents[1]
DATE = '2026-10-24'
OUT = ROOT / 'output' / ('phase1-' + DATE)
STAGE = OUT / 'team-assistant-phase1'
PDF = OUT / 'phase1-report.pdf'
if OUT.exists():
    raise ValueError('Release already exists; preserve the reviewed release')
OUT.mkdir(parents=True)
styles = getSampleStyleSheet()
styles.add(ParagraphStyle(name='Report', fontName='Helvetica', fontSize=10, leading=13, spaceAfter=7))
styles.add(ParagraphStyle(name='Small', fontName='Helvetica', fontSize=8.5, leading=11, spaceAfter=5))
flow = []
text_sections = []
def p(text, style='Report'):
    flow.append(Paragraph(text, styles[style]))
    text_sections.append(text)
def heading(text):
    p(text, 'Heading2')
def table(rows, widths):
    t = Table([[Paragraph(str(x), styles['Small']) for x in row] for row in rows], colWidths=widths)
    t.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),colors.HexColor('#EAF0F5')),('VALIGN',(0,0),(-1,-1),'TOP'),('BOTTOMPADDING',(0,0),(-1,-1),6),('LINEBELOW',(0,0),(-1,-1),.3,colors.HexColor('#CED6DE'))]))
    flow.append(t)
    text_sections.extend(' | '.join(map(str,row)) for row in rows)
p('Reliable Team Assistant', 'Title')
p('Phase 1: Dependency-Aware Recovery in an Oncall Team Assistant<br/>Yiting Dai | Solo | CSE291A Option 5 | October 24, 2026', 'Small')
heading('1. Research question and implemented mechanism')
p('After a team assistant consumes evidence that becomes invalid, when can it preserve diagnosis quality while avoiding unnecessary recomputation? Configuration and telemetry specialists query read-only Cloud-OpsBench snapshots; a coordinator produces a diagnosis. A trusted notification identifies the invalid source/version or artifact, never the correct answer. General error detection, live remediation and irreversible operational actions are outside this study.')
p('The implementation tracks actually consumed inputs and versions, invalidates the dependent closure, retains valid independent work, rejects obsolete writes and links local corrections to earlier publications. Observable missing provenance forces a full restart with the historical cost ledger retained. Unrecorded implicit dependencies remain a limitation. Scheduling concurrent investigations and learning procedural memory are gated extensions, not requirements for the recovery claim.')
heading('2. Workload, controls and scoring')
p('The pinned Cloud-OpsBench revision is ea05dafbc87b5985df8721622d63d4229c2b4224 [1]. Development uses already-seen Online Boutique incidents; constructed source changes are not production incident histories. No held-out observations were opened. Historical inference used tool-disabled Codex CLI, gpt-6-astra; its wrapper-inclusive task tokens are not API dollars. Labels enter post-inference native scoring, not agent prompts.')
p('No-recovery, restart and selective share the same saved fault-consumed prefix. Source-consistency prevention runs independently from the beginning and is charged its full cost. Native root accuracy (JRA) and evidence-path completeness (ECR) are reported alongside factual source fidelity and publication validity: a correct root with false support is not a clean success. Failures, uncertainty and unpropagated faults remain in the evidence. Repeated cases are not independent application samples.')
heading('3. October 24 real-model development evidence')
table([['Condition','Selective / restart calls','Incremental tokens','Interpretation'],['Sparse runtime','1 / 3','19,616 / 51,701','62.06% saving; both pass'],['Sparse performance','1 / 3','26,301 / 58,874','55.33% saving; both pass'],['Consumed handoff','2 / 3','36,620 / 52,499','30.25%; wider invalid closure'],['Wrong support','1 / 3','19,221 / 50,973','9556 repaired to 9555; root unchanged'],['All work expired','3 / 3','50,751 / 51,145','Zero reuse; identical requests']],[110,98,115,181])
p('Subtotal: 53 real calls and 953,990 known task tokens; no retries or unknown usage in these October24 batches. These are seen-case feasibility results, not a held-out or repeated-model study. All methods retain their own source audits and raw trajectories.', 'Small')
flow.append(PageBreak())
p('Findings, limitations and Phase 2 plan', 'Title')
heading('4. What the results establish')
p('Selective recovery reduces work when a valid independent branch remains. Actual telemetry-to-configuration handoff widens the invalidated closure to five artifacts and reduces savings. In the wrong-content pilot, a false supporting port propagates into the answer even while native JRA/ECR remain 1; explicit source fidelity catches the defect. This is supporting-fact repair, not wrong-root repair. Independent prevention succeeds at lower end-to-end cost in the sparse/wrong-content pilots, so it remains a strong comparator.')
p('When all work expires, both policies regenerate 7/7 artifacts, retain zero work, issue three byte-identical application requests and seven fresh tools. The 0.77% token difference is not a reuse gain; single-run timing and provider cache accounting do not establish speedup. Expired answers can still be factually correct. This settled-prefix control does not prove equivalent concurrency fencing.')
heading('5. Engineering validation and honest failure accounting')
p('The current development suite passes 185/185 tests. A new label-free injector canonical-sorts collected YAML observations and increments one eligible numeric-port field, preserving pristine source records. Offline replay checks exact repair, stale-publication rejection and unchanged telemetry. A revision-wrapped performance prefix cannot be matched to its original archive and is correctly rejected; its positive roundtrip separately uses original-source YAML replay. This does not establish new-model semantic propagation.')
p('Synthetic tracking optimization reduces dense-graph commit CPU median 69.141 to 3.521 ms at 48 nodes and 920.626 to 20.117 ms at 96 nodes (seven samples; sequential before/after). Sparse graphs show no consistent benefit. These are not real-model or production latency measurements. Historical October 21 verification timeout and its 60,000-token unknown reservation remain included separately; a successful single-request retry is not a successful original arm.')
heading('6. Frozen next study and resources')
p('The frozen design selects three distinct locked incident IDs, one per category, with two stochastic repetitions, fixed parallel-one topology, wrong-content and all-expiry conditions. Retain not-injected, unconsumed, failed and not-run outcomes; never replace cases based on outcomes. Primary quality is strict joint success including native ECR, source support and current publication; a semantic-only joint is separately labeled. Aggregate within incident before across incidents; no significance or population-p95 claim.')
p('The complete formal reservation is 102 calls / 6.12 million tokens, capped at $20; development has 18 calls / 1.08 million tokens and $5. Authenticated TritonAI inspection confirms 7 permitted routes, $50 remaining and RPM 100. Public GLM 5.3 pricing implies a $7.77 formal ceiling under enforced caps; this is not a measured charge. The key expires Nov 7, before the planned formal window. Provider/output-cap calibration, renewed access, dataset-acceptance confirmation and the launch annex remain gates. No formal launch is authorized.')
p('AI use: Codex assisted implementation, experiment tooling, analysis and report drafting. The student reviews these materials and remains responsible for the submission. [1] github.com/LLM4Ops/Cloud-OpsBench (MIT). [2] Course Option5 project specification and Piazza 45 (Phase 1 extension). [3] docs.tritonai.ucsd.edu/models (public rates, Oct24).', 'Small')
flow.append(PageBreak())
p('Appendix: milestones and reproducibility', 'Title')
p('Timeline appendix, separate from the two-page report.', 'Small')
table([['Date','Milestone','Completion criterion'],['Completed','Recovery prototype and development controls','Sparse/widened dependency, wrong support, total expiry, source repair, failure ledgers and tracking overhead'],['Oct 25-Nov 2','Provider preparation and Phase 1 review','Validate authorized TritonAI model/output accounting on seen data; verify package and course upload requirements'],['Nov 4','Phase 1 due','1-2page report plus timeline; code/data/instructions/results; exact cutoff and upload item still need confirmation'],['Before Nov 7','API continuity','Course key renewal or confirmed continuation; never assume a later batch has access'],['Nov 5-12','Close launch gates','Generic real-model calibration, source acceptance, model/code hashes, scoring rubric and full budget reservation'],['Nov 13-23','Formal evaluation, only after gates','Three selected incidents, two repeats; locked until ready; all failures retained'],['Nov 24-30','Analysis and optional extensions','Boundary analysis first; concurrent scheduling then gated learning only if affordable'],['Dec 1-10','Final artifact/report','Clean reproduction; final 4-6 pages; submission deadline Dec 10']],[82,145,277])
heading('Artifact map and offline checks')
p('The ZIP includes src/tests/configs/scripts; frozen protocols and compact result tables; raw model requests, responses, usage and source audits; the pinned evaluator/three development label directories; this report; and a SHA256 manifest. Course-only source exports and credentials are excluded. Previous releases remain historical. Source manifests describe the commits used in each experiment; the new package separately identifies its current source revision.')
p('Run verify_phase1_package.py for hashes, early matrix, native scores, controls and tests; verify_phase1_release.py for the separate October 21 ledger; verify_phase1_oct24.py for new raw/compact counts, regenerated scores, all-expiry request equivalence and generic mechanics. Verification is offline and invokes no model. Snapshot-dependent tests may skip with the bundled-only dataset subset; the verification receipt states actual counts.')
p('The main report summarizes development evidence, not production reliability or broad malicious-input protection. Tools are read-only, publication is a local simulation, and automatic semantic error detection is excluded. Full benchmark snapshots are acquired separately at the pinned revision for authorized development; final-set content is not packaged. Phase 1 is prepared for review, not submitted by this build.')
def footer(canvas, doc):
    canvas.setFont('Helvetica',8)
    canvas.drawString(54,28,'Reliable Team Assistant | Phase 1 | October 24, 2026')
    canvas.drawRightString(558,28,str(doc.page))
SimpleDocTemplate(str(PDF),pagesize=letter,leftMargin=54,rightMargin=54,topMargin=40,bottomMargin=44).build(flow,onFirstPage=footer,onLaterPages=footer)
(OUT/'phase1-report.txt').write_text('\n\n'.join(text_sections)+'\n')
STAGE.mkdir()
def copy(path, relative):
    target = STAGE / relative
    target.parent.mkdir(parents=True,exist_ok=True)
    if path.is_dir():
        shutil.copytree(path,target,ignore=shutil.ignore_patterns('__pycache__','*.pyc','.DS_Store'))
    else:
        shutil.copy2(path,target)
for name in ('src','scripts','tests','configs','pyproject.toml','requirements.lock','requirements-report.txt'):
    copy(ROOT/name,name)
for name in ('phase1-matrix-results.json','phase1-controls.json','current-proposal.txt','experiment-plan.txt','semantic-recovery-pilot-rubric.txt','semantic-recovery-20261021.txt','semantic-recovery-20261021-results.json','semantic-verification-retry-20261021.json','recovery-completion-20261021.txt','recovery-overhead-results.json','provenance-fallback-results.json'):
    copy(ROOT/'docs'/name,Path('docs')/name)
for name in ('phase1-matrix','phase1-port-corruption-v1','phase1-integrity-v2','phase1-controls.json','semantic-recovery-20261021','semantic-verification-retry-20261021','fallback-20261021','overhead-20261021'):
    copy(ROOT/'runs'/name,Path('runs')/name)
for stem in ('cross-case-recovery-20261024','recovery-boundaries-20261024','all-expiry-20261024'):
    for suffix in ('.txt','-results.json','-protocol.txt'):
        copy(ROOT/'docs'/(stem+suffix),Path('docs')/(stem+suffix))
for name in ('recovery-overhead-20261024.txt','recovery-overhead-20261024.json','evidence-map-20261024.txt','formal-evaluation-v1.txt','course-requirements-audit-20261024.txt','tritonai-verification-20261024.txt','tritonai-verification-20261024.json','generic-fault-validation-20261024.txt','generic-fault-validation-20261024.json','generic-fault-tests-20261024.log'):
    copy(ROOT/'docs'/name,Path('docs')/name)
for name in ('cross-case-recovery-20261024','recovery-boundaries-20261024','all-expiry-20261024','overhead-20261024-before','overhead-20261024-after'):
    copy(ROOT/'runs'/name,Path('runs')/name)
copy(PDF,'phase1-report.pdf')
copy(OUT/'phase1-report.txt','phase1-report.txt')
copy(ROOT/'docs/phase1-oct24-readme.txt','RELEASE_README.txt')
vendor = ROOT.parent/'preparation/291a-team-agent/vendor/Cloud-OpsBench'
for rel in ('agents/cloudops_agent','LICENSE','process-label/boutique/runtime/1','process-label/boutique/service/1','process-label/boutique/performance/1'):
    copy(vendor/rel,Path('vendor_offline/Cloud-OpsBench')/rel)
files={str(p.relative_to(STAGE)):hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(STAGE.rglob('*')) if p.is_file()}
for name in files:
    if Path(name).suffix in ('.pem','.key') or Path(name).name in ('auth.json','.env'):
        raise ValueError('Credential-like file: '+name)
manifest={'source_commit':subprocess.check_output(['git','-C',str(ROOT),'rev-parse','HEAD'],text=True).strip(),'dataset_commit':'ea05dafbc87b5985df8721622d63d4229c2b4224','files':files}
(STAGE/'MANIFEST.sha256.json').write_text(json.dumps(manifest,indent=2)+'\n')
archive=OUT/'phase1-submission.zip'
with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED) as z:
    for path in sorted(STAGE.rglob('*')):
        if path.is_file(): z.write(path,str(path.relative_to(STAGE.parent)))
print(archive)
