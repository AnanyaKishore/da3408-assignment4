# DA3408 - Assignment 4

## Structure
```text
da3408-assignment4/
├── grafana/
│   └── dashboard.json
├── .gitignore
├── AI_DISCLOSURE.md
├── alertmanager.yml
├── app.py
├── metrics.py
├── prometheus.yml
├── README.md
├── requirements.txt
└── rules.yml
```
The Prometheus, node_exporter and Alertmanager binaries, their runtime data (`data/`, `am-data/`) and the test images (`sample_images/`) are git-ignored. The binaries must be downloaded separately (instructions below).

The Streamlit app was tested with the following inputs:

```text
sample_images/
├── bulk_job_1.zip
├── bulk_job_2.zip # also contains .txt files, to check that non-images are skipped
├── bulk_job_3.zip # contains corrupted images
├── images.zip # contains 27 images
├── single_1.jpg
├── single_2.jpg
└── single_3.jpg
```

## Setup

Tested with Prometheus 3.15.0, node_exporter 1.12.1, Alertmanager 0.34.1 and Grafana (Docker) on a single Linux laptop with 12 CPU cores and about 8 GB of RAM. Inference runs entirely on the CPU.

```bash
git clone https://github.com/AnanyaKishore/da3408-assignment4.git
cd da3408-assignment4
python3 -m venv .venv
source .venv/bin/activate
pip install torch --index-url https://download.pytorch.org/whl/cpu
pip install -r requirements.txt
```

Download the Linux amd64 releases of Prometheus, node_exporter and Alertmanager from https://prometheus.io/download/ and extract them in the repository root, so that the folders are:

```text
prometheus-3.15.0.linux-amd64/
node_exporter-1.12.1.linux-amd64/
alertmanager-0.34.1.linux-amd64/
```

## Where to look

| File | Purpose |
|------|---------|
| `app.py` | Streamlit application. Captions images with `Salesforce/blip-image-captioning-base` on the CPU, in **Single** mode (one image) or **Bulk** mode (a ZIP of images). Non-image, hidden and oversized files inside a ZIP are skipped, and unreadable images are counted as failures without crashing the app. |
| `metrics.py` | All Prometheus metrics, defined with `prometheus_client`: Counters, Gauges, Histograms, Summaries and an Info metric, with the custom labels `mode`, `status`, `client_ip` and `session_id`. They live in a separate module because Streamlit re-runs `app.py` on every interaction, which would otherwise register the metrics twice. The `/metrics` endpoint is served on a separate port (18000). |
| `prometheus.yml` | Scrape configuration for the app (`localhost:18000`), node_exporter (`localhost:9100`) and Prometheus itself, every 5 secs. Also loads `rules.yml` and points to Alertmanager. |
| `rules.yml` | Recording rules (pre-computed PromQL used by the dashboard: CPU %, RAM %, disk, network, latency percentiles, error ratio) and alert rules (app down, node_exporter down, high CPU, high RAM, slow inference, high error rate). Explanations are commented above each PromQL. |
| `alertmanager.yml` | Routes firing alerts to a webhook (replace the URL with your own from https://webhook.site). |
| `grafana/dashboard.json` | Grafana dashboard export, ordered from high-level health to low-level resources: service health, throughput, latency, app/host correlation, system resources, workload. |

---

# Running the app and the monitoring stack

Run each component in its own terminal from the repository root, i.e., from inside the `da3408-assignment4` folder:

## Streamlit app

```bash
# Terminal 1
source .venv/bin/activate
streamlit run app.py
```
Open http://localhost:8501 once in the browser. The model loads and the metrics server starts only when the page is first opened (the first run downloads about 1 GB of model weights). Then check http://localhost:18000/metrics, which should list metrics starting with `caption_`, including `caption_model_loaded 1.0`.

## node_exporter

```bash
# Terminal 2
./node_exporter-1.12.1.linux-amd64/node_exporter
```
Check http://localhost:9100/metrics, which should list `node_cpu_seconds_total` and `node_memory_*` metrics.

## Prometheus

```bash
# Terminal 3
./prometheus-3.15.0.linux-amd64/promtool check config prometheus.yml
./prometheus-3.15.0.linux-amd64/prometheus --config.file=prometheus.yml --storage.tsdb.path=./data
```
`promtool` should report `SUCCESS` for both `prometheus.yml` and `rules.yml`. Then check:
- http://localhost:9090/targets: `caption_app`, `node` and `prometheus` are all **UP**.
- http://localhost:9090/rules: all rule groups are listed with health **OK**.
- http://localhost:9090/query: running `node:cpu_utilisation:percent` returns a value between 0 and 100.

## Alertmanager

```bash
# Terminal 4
./alertmanager-0.34.1.linux-amd64/alertmanager --config.file=alertmanager.yml --storage.path=./am-data
```
Check http://localhost:9093 (Alertmanager UI) and http://localhost:9090/alerts, where all alerts should be **Inactive**. To test the pipeline, stop the Streamlit app with `Ctrl+C`: after about 15 seconds, `CaptionAppDown` fires and appears on the webhook.

## Grafana

```bash
# Terminal 5
docker start grafana
```
1. Open http://localhost:3000.
2. Go to **Connections -> Data sources -> Add data source -> Prometheus** and set the server URL to `http://172.17.0.1:9090`. Grafana runs inside Docker, where `localhost` refers to the container itself, so it must reach Prometheus through the Docker bridge address (or the machine's Wi-Fi IP). Click **Save & test**.
3. Go to **Dashboards -> New -> Import**, upload `grafana/dashboard.json` and select the Prometheus data source.
4. Set the time range to a relative range (e.g. **Last 30 minutes**) and the refresh to **5s**.

Upload a single image and a ZIP in the Streamlit app to see the throughput, latency and host resource panels respond.

## Shutting down

```bash
# Ctrl+C in Terminals 1 to 4, then:
docker stop grafana
```

---

Author: Ananya Kishore (DA24B035)