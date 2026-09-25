"""Run via python -m evaluation.run; accuracy is explicitly human-reviewed."""
import argparse
import json
from pathlib import Path
from time import perf_counter
import uuid

import httpx

from app.config import get_settings
from app.db import Database


def summary(results):
    out = {}
    for language in ('en', 'zh'):
        rows = [r for r in results if r['language'] == language]
        reviewed = len(rows) == 10 and all(isinstance(r.get('pass'), bool) for r in rows)
        out[language] = {
            'reviewed': reviewed,
            'correct': sum(r.get('pass') is True for r in rows),
            'accepted': reviewed and sum(r.get('pass') is True for r in rows) >= 8
                and all(r.get('pass') is True for r in rows if r['id'] >= 8)
                and all(r.get('request_accepted_seconds') is not None and r['request_accepted_seconds'] < 3 for r in rows)
                and all(not r.get('error') for r in rows),
        }
    return out


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--base-url', default='http://localhost:8000')
    parser.add_argument('--output', default='reports/evaluation.json')
    parser.add_argument('--review', action='store_true', help='Review an existing report without API calls')
    args = parser.parse_args()
    output = Path(args.output)
    if args.review:
        report = json.loads(output.read_text(encoding='utf-8'))
        for row in report['results']:
            print(json.dumps(row, ensure_ascii=False, indent=2))
            while True:
                decision = input('Correct numbers, assumptions and context? [y/n]: ').strip().lower()
                if decision in ('y', 'n'):
                    break
            row['pass'] = decision == 'y'
            row['review_note'] = input('Review note: ')
        report['summary'] = summary(report['results'])
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps(report['summary'], indent=2))
        return
    settings = get_settings()
    db = Database(settings.database_url)
    cases = json.loads(Path(__file__).with_name('cases.json').read_text(encoding='utf-8'))
    report = {'model': settings.openai_model, 'results': [],
              'review_instructions': 'Compare every requested group/value to reference rows. Money tolerance CAD 0.01; percentages 0.01 percentage points. Require correct units, denominator, completed-order filter and city follow-up context. SQL need not match textually. Do not approve fabricated numbers.'}
    output.parent.mkdir(parents=True, exist_ok=True)
    def save():
        report['summary'] = summary(report['results'])
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    try:
        with httpx.Client(timeout=120) as client:
            for language in ('en', 'zh'):
                sessions = {}
                for case in cases:
                    session = sessions.setdefault(case['session_group'], uuid.uuid4().hex)
                    row = {'id': case['id'], 'language': language, 'question': case[language],
                           'session_id': session, 'reference_sql': case['reference_sql'],
                           'expected': db.run_select(case['reference_sql']).to_dict(),
                           'answer': '', 'sql': [], 'pass': None,
                           'request_accepted_seconds': None, 'first_tool_seconds': None,
                           'first_answer_token_seconds': None}
                    start = perf_counter()
                    done = False
                    try:
                        with client.stream('POST', args.base_url+'/chat/stream', json={'session_id': session, 'question': case[language]}) as response:
                            response.raise_for_status()
                            for line in response.iter_lines():
                                if not line.startswith('data: '):
                                    continue
                                event = json.loads(line[6:])
                                elapsed = perf_counter()-start
                                metric = {'session': 'request_accepted_seconds', 'tool_call': 'first_tool_seconds', 'token': 'first_answer_token_seconds'}.get(event['type'])
                                if metric and row[metric] is None:
                                    row[metric] = elapsed
                                if event['type'] == 'done':
                                    row.update(answer=event['answer'], sql=event['sql'])
                                    done = True
                                if event['type'] == 'error':
                                    raise RuntimeError(event['message'])
                        if not done:
                            raise RuntimeError('Stream ended without done')
                    except Exception as exc:
                        row.update(error=str(exc), **{'pass': False})
                    row['total_seconds'] = perf_counter()-start
                    report['results'].append(row)
                    save()
                    print(language, case['id'], 'error' if row.get('error') else 'awaiting review')
    finally:
        db.engine.dispose()
        save()


if __name__ == '__main__':
    main()
