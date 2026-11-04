# Reproducible benchmark scenarios

Start the Compose stack and configure the policy first. Run the pinned Locust image with identical users, spawn rate, duration, host and client identity distribution for each algorithm:

```sh
docker compose --profile benchmarks build locust
docker compose --profile benchmarks run --rm --no-deps locust --host http://nginx -u 20 -r 5 -t 15s --csv /reports/token-bucket
python benchmarks/export_json.py benchmarks/reports/token-bucket_stats.csv benchmarks/reports/token-bucket.json
```

Change the policy to `sliding_window` using the authenticated API and repeat the same command. HTTP 429 is counted as an expected rate-limit outcome; 5xx and transport errors remain failures. Capture accepted/rejected deltas from `/api/metrics` immediately before and after each run. Preserve environment, Compose image tags, policy, machine size and workload settings alongside results. No benchmark values are prepopulated.
