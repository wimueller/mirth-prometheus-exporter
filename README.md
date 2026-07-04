# Prometheus Python Exporter for Mirth Connect

This Python script serves as a Prometheus exporter for Mirth Connect, allowing users to collect various metrics related to message processing and server health. Mirth Connect is an open-source integration engine that facilitates the exchange of healthcare information.

## Features
- **Metrics Collection**: Collects metrics such as messages received, sent, filtered, and errored per channel, as well as the current size of the Mirth server's storage.
- **Server Health Monitoring**: Monitors the state of the Mirth server and reports it as either up (0) or down (1).
- **Dynamic Configuration**: Configuration is read from a JSON file (`mirthConfig.json`) and can be overridden via environment variables, allowing users to specify the Mirth instance to monitor and authentication credentials.
- **Session Reuse**: The exporter logs in once and reuses the session across scrapes; if the session expires, it automatically logs in again.

## Requirements
- Python 3.x
- `prometheus_client` library (`pip install prometheus_client`)
- `mirthpy` library (`pip install mirthpy`)
- A running [Prometheus](https://prometheus.io/) server that scrapes the exporter's metrics endpoint. The exporter only exposes the metrics — a Prometheus server is required to collect and store the data (see `prometheus.yml` for an example scrape configuration).

## Configuration
The exporter requires a `mirthConfig.json` file in the same directory with the following structure:
```json
{
  "instance": "MIRTH_INSTANCE_URL",
  "username": "USERNAME",
  "password": "PASSWORD",
  "prometheusPort": PROMETHEUS_PORT_NUMBER
}
```

To specify a certain port to point at for Mirth, you can add `mirthPort` in the `mirthConfig.json` file. (Mirth's default port is 8443)
```json
{
  "instance": "MIRTH_INSTANCE_URL",
  "username": "USERNAME",
  "password": "PASSWORD",
  "mirthPort": MIRTH_PORT_NUMBER,
  "prometheusPort": PROMETHEUS_PORT_NUMBER
}
```

Alternatively (or additionally), configuration values can be provided via environment variables, which take precedence over `mirthConfig.json`. This is especially useful when running the exporter in a container:

| Environment variable | Config file key  |
|----------------------|------------------|
| `MIRTH_INSTANCE`     | `instance`       |
| `MIRTH_USERNAME`     | `username`       |
| `MIRTH_PASSWORD`     | `password`       |
| `MIRTH_PORT`         | `mirthPort`      |
| `PROMETHEUS_PORT`    | `prometheusPort` |

## Metrics

### Per channel
- **mirth_messages_received_total** (counter): Number of messages received per channel.
- **mirth_messages_sent_total** (counter): Number of messages sent per channel.
- **mirth_messages_filtered_total** (counter): Number of messages filtered per channel.
- **mirth_messages_errored_total** (counter): Number of messages that encountered errors per channel.
- **mirth_messages_queued** (gauge): Number of messages currently queued per channel.
- **mirth_channel_state** (gauge): Deploy state of a channel as an enum — one time series per state (`STARTED`, `PAUSED`, `STOPPED`), with `1` for the current state and `0` otherwise.
- **mirth_channel_deployed_revision_delta** (gauge): Number of saved revisions since the channel was last deployed. A value greater than `0` means the channel has been modified but not redeployed.

### Server health
- **mirth_server_state** (gauge): State of the Mirth server (1 for up, 0 for down).
- **mirth_server_size** (gauge): Used storage of the Mirth server in bytes.
- **mirth_disk_free_bytes** / **mirth_disk_total_bytes** (gauge): Free and total disk space of the Mirth server in bytes.
- **mirth_cpu_usage_percent** (gauge): CPU usage as reported by the Mirth API.
- **mirth_memory_allocated_bytes** / **mirth_memory_free_bytes** / **mirth_memory_max_bytes** (gauge): JVM memory usage of the Mirth server.
- **mirth_channels_total** (gauge): Number of channels configured on the server.
- **mirth_server_info**: Info metric carrying the Mirth server version as a label.

> **Note (breaking change):** `mirth_server_state` now follows the Prometheus convention (`1` = up, `0` = down). In earlier versions the values were inverted. The message counters (`*_total`) are now exposed with the correct `counter` type, so `rate()`/`increase()` queries work as expected.
## Usage
1. Configure mirthConfig.json with the appropriate values.
2. Run the script (python mirth_exporter.py).
3. Access the metrics via Prometheus at the specified port (http://localhost:{prometheusPort} by default).

## Important Note
Ensure that the Mirth Connect server is reachable from the machine running the exporter, and the specified credentials have appropriate permissions for accessing Mirth's APIs.