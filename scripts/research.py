"""Validate research provenance and assess source-to-atom fidelity.

Offline by default. Model calls require --alignment-model and a positive call
budget. Markdown export requires aligned evidence unless --draft is explicit.
"""
import argparse
import json
from pathlib import Path

from gleipnir.alignment import AlignmentReport, assess, gate
from gleipnir.rawstore import RawStore
from gleipnir.research import ResearchResult, render, save, validate


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('manifest', type=Path)
    parser.add_argument('--store', type=Path, required=True)
    parser.add_argument('--save', action='store_true', help='Append a research snapshot, including drafts')
    parser.add_argument('--markdown', type=Path)
    parser.add_argument('--draft', action='store_true', help='Export with unchecked or blocked evidence explicitly labelled')
    group = parser.add_mutually_exclusive_group()
    group.add_argument('--pretrained', action='store_true', help='Run the local pretrained source-support baseline')
    parser.add_argument('--model-directory', type=Path, default=Path('raw/models/nli-MiniLM2-L6-H768'))
    group.add_argument('--alignment-model', help='Explicit Anthropic model ID; API key from ANTHROPIC_API_KEY')
    group.add_argument('--alignment-report', type=Path, help='Replay a previously saved alignment report offline')
    parser.add_argument('--max-alignment-calls', type=int, default=0)
    parser.add_argument('--alignment-output', type=Path)
    parser.add_argument('--context-margin', type=int, help='Characters on each side of quote; defaults to 600 for pretrained, 1200 for LLM')
    parser.add_argument('--alignment-threshold', type=float, default=0.8,
                        help='Experimental routing threshold, not calibrated probability')
    args = parser.parse_args()
    if args.max_alignment_calls < 0 or (args.max_alignment_calls and not (args.alignment_model or args.pretrained)):
        parser.error('a nonnegative call budget and an explicit alignment model are required')
    try:
        result = ResearchResult.model_validate_json(args.manifest.read_bytes())
        store = RawStore(args.store)
        checks = validate(result, store)
        alignment = None
        if args.pretrained:
            from gleipnir.pretrained import PretrainedNLIBackend
            alignment = assess(result, store, PretrainedNLIBackend(args.model_directory),
                               max_calls=args.max_alignment_calls, threshold=args.alignment_threshold,
                               context_margin=args.context_margin if args.context_margin is not None else 600)
        elif args.alignment_model:
            from gleipnir.oracle import AlignmentOracle
            # Cache-only replay does not need credentials or even the SDK.
            client = None
            if args.max_alignment_calls:
                import anthropic
                client = anthropic.Anthropic(max_retries=0, timeout=45.0)
            try:
                alignment = assess(result, store,
                                   AlignmentOracle(client.messages if client else None,
                                                   model=args.alignment_model),
                                   max_calls=args.max_alignment_calls,
                                   threshold=args.alignment_threshold,
                                   context_margin=args.context_margin if args.context_margin is not None else 1200)
            finally:
                if client:
                    client.close()
        elif args.alignment_report:
            alignment = AlignmentReport.model_validate_json(args.alignment_report.read_bytes())
        if alignment:
            checks['alignment'] = gate(result, store, alignment)
            if args.alignment_output:
                args.alignment_output.write_text(alignment.model_dump_json(indent=2)+'\n', encoding='utf-8')
        elif args.alignment_output:
            raise ValueError('alignment output requires a model or an existing report')
        if args.save:
            checks['snapshot_hash'] = save(result, store)
        if args.markdown:
            eligible = checks.get('alignment', {}).get('synthesis_evidence_eligible', False)
            if not args.draft and not eligible:
                raise ValueError('synthesis evidence is unchecked or blocked; assess/revise atoms or use --draft')
            note = render(result)
            if eligible:
                note = note.replace('DRAFT — source fidelity has not been certified by this renderer.',
                                    'Source alignment gate passed for cited atoms. Synthesis remains a proposal; '
                                    'its reasoning and real-world truth are not verified.')
            if alignment:
                note += '\nAlignment gate: ' + json.dumps(checks['alignment']) + '\n'
            args.markdown.write_text(note, encoding='utf-8')
    except (ValueError, OSError, ImportError) as exc:
        parser.exit(1, f'Research validation failed: {exc}\n')
    print(json.dumps(checks, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
