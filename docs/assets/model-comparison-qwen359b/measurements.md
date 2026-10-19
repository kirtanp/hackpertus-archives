# qwen3.5-9B Measurements

## Committed Baseline
- Model: apertus-v1-5-70b
- Runs: 48, Passed: 46 (96%)
- Median: 7.8s
- Means: schools=6/6, teachers=6/6, consultations=9/9, pupils=10/12 (83%), condition=3/3, pay=3/3, lookup=9/9

## qwen3.5-9B
- Model: qwen3.5-9b
- Endpoint: https://api.melious.ai/v1
- Authentication: Required (key in home config)
- Test: Melious endpoint available, 401 Unauthorized without key

## Comparison
Small model vs deployed baseline:
- This is an uneven comparison (9B vs 70B)
- qwen3.5-9B: melious hosted, needs auth
- Baseline: local apertus-v1-5-70b Commit