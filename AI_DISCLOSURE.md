# Use of AI

**Tools used:** Claude


**How they were used:**
1. To provide an overview of Streamlit apps and how they are structured.
2. Generating the boilerplate code for the Streamlit application `app.py`, and understanding Streamlit syntax, such as how the script re-runs on every interaction, `st.cache_resource`, tabs and file uploaders
3. Understanding the various metric types available in `prometheus_client`, namely Counter, Gauge, Histogram and Summary, and how each is exported (`_total`, `_bucket`, `_sum`, `_count`)
4. Brainstorming which metrics were suitable for an image captioning application (e.g. images processed by mode and status, active requests, inference latency, upload size, model memory usage), and which custom labels to add for client tracking (`client_ip` and `session_id`)
5. Generating the boilerplate code for `metrics.py`, and understanding why the metrics had to be defined in a separate module (so that Streamlit's re-runs do not register them twice)
6. Generating the PromQL queries for the recording and alert rules in `rules.yml`
7. Aiding with the layout and design of the Grafana dashboard (`grafana/dashboard.json`), including the flow from high-level health to low-level resource metrics and adherence to the 7 Commandments of Plotting. I then requested changes to titles, panel sizes, legends and the colour palette after viewing the dashboard myself
8. Understanding what changes had to be made to the `prometheus.yml` file used in class, and how to run Prometheus, node_exporter and Alertmanager from the repository root instead of from inside the `prometheus-3.15.0.linux-amd64/` folder as done in class, along with the terminal commands for each.
9. Debugging the Grafana data source connection: because Grafana runs inside Docker, `localhost` refers to the container, so the Prometheus URL had to be set to the Docker bridge address (`172.17.0.1:9090`).


**Impact:**

LLMs greatly reduced the time I would have otherwise spent writing large chunks of code for the Streamlit application, allowing me to build on top of the boilerplate code and granting me the freedom to understand more fully how Prometheus and Grafana work.