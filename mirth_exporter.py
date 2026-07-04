import json
import os
import time
from prometheus_client.core import CounterMetricFamily, GaugeMetricFamily, InfoMetricFamily, REGISTRY
from prometheus_client import start_http_server, GC_COLLECTOR, PLATFORM_COLLECTOR, PROCESS_COLLECTOR
import urllib3
from mirthpy.mirthService import MirthService
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# channel states reported by mirth that always get a time series
CHANNEL_STATES = ('STARTED', 'PAUSED', 'STOPPED')

def loadConfig():
    # config file is optional if everything is provided via environment variables
    dir_path = os.path.dirname(os.path.realpath(__file__))
    config_path = os.path.join(dir_path, 'mirthConfig.json')
    config = {}
    if os.path.exists(config_path):
        with open(config_path) as f:
            config = json.load(f)

    # environment variables take precedence over mirthConfig.json
    overrides = {
        'instance': os.environ.get('MIRTH_INSTANCE'),
        'username': os.environ.get('MIRTH_USERNAME'),
        'password': os.environ.get('MIRTH_PASSWORD'),
        'mirthPort': os.environ.get('MIRTH_PORT'),
        'prometheusPort': os.environ.get('PROMETHEUS_PORT'),
    }
    config.update({key: value for key, value in overrides.items() if value})

    missing = [key for key in ('instance', 'username', 'password', 'prometheusPort') if not config.get(key)]
    if missing:
        raise SystemExit("Missing configuration values: {}. Provide them in mirthConfig.json or via environment variables.".format(", ".join(missing)))

    config['prometheusPort'] = int(config['prometheusPort'])
    return config

class MirthStatsCollector(object):
    def __init__(self, config):
        self.config = config
        self.instance = config['instance']

        if self.instance == 'localhost':
            self.instance = 'host.docker.internal'

        # session is kept open between scrapes and re-established on demand
        self.service = None

    def openService(self):
        if self.config.get('mirthPort'):
            service = MirthService(instance=self.instance, username=self.config['username'], password=self.config['password'], port=self.config.get('mirthPort'))
        else:
            service = MirthService(instance=self.instance, username=self.config['username'], password=self.config['password'])
        service.open()
        return service

    def gather(self):
        if self.service is None:
            self.service = self.openService()

        return {
            'version': self.service.version,
            'systemStats': self.service.getSystemStats(),
            'idsAndNames': self.service.getChannelIdsAndNames(),
            'channelStatistics': self.service.getChannelStatistics(),
            'channelStatuses': self.service.getChannelStatuses(),
        }

    def collect(self):
        # metrics to gather
        messageRec = CounterMetricFamily("mirth_messages_received_total", "How many messages have been received (per channel).", labels=["instance", "channelName", "channelId"])
        messageSent = CounterMetricFamily("mirth_messages_sent_total", "How many messages have been sent (per channel).", labels=["instance", "channelName", "channelId"])
        messageFiltered = CounterMetricFamily("mirth_messages_filtered_total", "How many messages have been filtered (per channel).", labels=["instance", "channelName", "channelId"])
        messageError = CounterMetricFamily("mirth_messages_errored_total", "How many messages have errored (per channel).", labels=["instance", "channelName", "channelId"])
        messageQueued = GaugeMetricFamily("mirth_messages_queued", "How many messages are currently queued (per channel).", labels=["instance", "channelName", "channelId"])
        memoryGauge = GaugeMetricFamily("mirth_server_size", "Mirth server storage size (used bytes).", labels=["instance"])
        diskFreeGauge = GaugeMetricFamily("mirth_disk_free_bytes", "Free disk space on the Mirth server in bytes.", labels=["instance"])
        diskTotalGauge = GaugeMetricFamily("mirth_disk_total_bytes", "Total disk space on the Mirth server in bytes.", labels=["instance"])
        cpuGauge = GaugeMetricFamily("mirth_cpu_usage_percent", "CPU usage of the Mirth server as reported by the Mirth API.", labels=["instance"])
        memAllocatedGauge = GaugeMetricFamily("mirth_memory_allocated_bytes", "Memory allocated by the Mirth JVM in bytes.", labels=["instance"])
        memFreeGauge = GaugeMetricFamily("mirth_memory_free_bytes", "Free memory within the Mirth JVM in bytes.", labels=["instance"])
        memMaxGauge = GaugeMetricFamily("mirth_memory_max_bytes", "Maximum memory available to the Mirth JVM in bytes.", labels=["instance"])
        channelCountGauge = GaugeMetricFamily("mirth_channels_total", "Number of channels configured on the Mirth server.", labels=["instance"])
        channelStateGauge = GaugeMetricFamily("mirth_channel_state", "Deploy state of a channel, 1 for the current state and 0 otherwise (per channel and state).", labels=["instance", "channelName", "channelId", "state"])
        revisionDeltaGauge = GaugeMetricFamily("mirth_channel_deployed_revision_delta", "Number of saved revisions since the channel was last deployed (per channel).", labels=["instance", "channelName", "channelId"])
        serverInfo = InfoMetricFamily("mirth_server", "Information about the Mirth server.", labels=["instance"])
        mirthServerStateGauge = GaugeMetricFamily("mirth_server_state", "Mirth server state, 1 means mirth server is up & 0 means mirth server is down", labels=["instance"])

        instance = self.instance
        data = None

        # first attempt may fail on an expired session, so retry once with a fresh login
        for attempt in range(2):
            try:
                data = self.gather()
                break
            except Exception:
                if self.service is not None:
                    try:
                        self.service.close()
                    except Exception:
                        pass
                self.service = None

        if data is not None:
            mirthServerStateGauge.add_metric([instance], 1) # 1 -> up

            serverInfo.add_metric([instance], {'version': data['version']})

            systemStats = data['systemStats']
            memoryGauge.add_metric([instance], systemStats.diskTotalBytes - systemStats.diskFreeBytes)
            diskFreeGauge.add_metric([instance], systemStats.diskFreeBytes)
            diskTotalGauge.add_metric([instance], systemStats.diskTotalBytes)
            cpuGauge.add_metric([instance], float(systemStats.cpuUsagePct or 0))
            memAllocatedGauge.add_metric([instance], systemStats.allocatedMemoryBytes)
            memFreeGauge.add_metric([instance], systemStats.freeMemoryBytes)
            memMaxGauge.add_metric([instance], systemStats.maxMemoryBytes)

            channelCountGauge.add_metric([instance], len(data['idsAndNames'].idsAndNames))

            # gather statistics on channels
            for stats in data['channelStatistics'].channelStatistics:
                # try to find channel name and id
                channelIdName = [(c.id, c.name) for c in data['idsAndNames'].idsAndNames if c.id == stats.channelId]

                # continue if can't find it
                if len(channelIdName) == 0:
                    continue

                channelId, channelName = channelIdName[0]

                # add to prometheus
                messageRec.add_metric([instance, channelName, channelId], int(stats.received))
                messageSent.add_metric([instance, channelName, channelId], int(stats.sent))
                messageError.add_metric([instance, channelName, channelId], int(stats.error))
                messageFiltered.add_metric([instance, channelName, channelId], int(stats.filtered))
                messageQueued.add_metric([instance, channelName, channelId], int(stats.queued))

            # gather deploy state per channel
            for status in data['channelStatuses'].dashboardStatuses:
                states = CHANNEL_STATES if status.state in CHANNEL_STATES else CHANNEL_STATES + (status.state,)
                for state in states:
                    channelStateGauge.add_metric([instance, status.name, status.channelId, state], 1 if status.state == state else 0)

                revisionDeltaGauge.add_metric([instance, status.name, status.channelId], int(status.deployedRevisionDelta or 0))
        else:
            mirthServerStateGauge.add_metric([instance], 0) # 0 -> down

        # push metrics to end point
        yield messageRec
        yield messageSent
        yield messageError
        yield messageFiltered
        yield messageQueued
        yield mirthServerStateGauge
        yield memoryGauge
        yield diskFreeGauge
        yield diskTotalGauge
        yield cpuGauge
        yield memAllocatedGauge
        yield memFreeGauge
        yield memMaxGauge
        yield channelCountGauge
        yield channelStateGauge
        yield revisionDeltaGauge
        yield serverInfo

if __name__ == "__main__":
    config = loadConfig()
    start_http_server(config['prometheusPort'])
    REGISTRY.register(MirthStatsCollector(config))
    REGISTRY.unregister(GC_COLLECTOR)
    REGISTRY.unregister(PLATFORM_COLLECTOR)
    REGISTRY.unregister(PROCESS_COLLECTOR)
    while True:
        # period between collection
        time.sleep(1)
