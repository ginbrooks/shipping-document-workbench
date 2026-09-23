"""Run an offline example using only synthetic fixtures, with no private templates."""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from shipping.demo import synthetic
from shipping.planning import calculate_plan
from shipping.templating import build_context


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', default='demo-output')
    args = parser.parse_args()
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    shipment = synthetic(two_contracts=True)
    plan = calculate_plan(shipment)
    contexts = {}
    for contract in shipment['contracts']:
        scope = contract['id']
        contexts[scope] = {
            kind: build_context(shipment, plan, kind, scope)
            for kind in ('customer', 'customs')
        }
        assert 'customs' not in json.dumps(contexts[scope]['customer'], default=str)
        assert 'customer' not in json.dumps(contexts[scope]['customs'], default=str)
    for name, value in [('synthetic-input', shipment), ('pallet-plan', plan), ('document-contexts', contexts)]:
        (output / (name + '.json')).write_text(
            json.dumps(value, ensure_ascii=False, indent=2, default=str) + '\n', encoding='utf-8'
        )
    summary = {
        'synthetic_only': True,
        'network_calls': 0,
        'contracts': len(shipment['contracts']),
        'products': len(shipment['lines']),
        'pallets': len(plan['pallets']),
        'notice': '演示输出仅含虚构数据和计算上下文，不是可用于出运的正式单证。',
    }
    (output / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == '__main__':
    main()
