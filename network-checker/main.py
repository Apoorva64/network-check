import asyncio
import logging
import os
import time
from prometheus_client import CollectorRegistry, Gauge, push_to_gateway
import requests
from kubernetes import client, config
from prometheus_client.exposition import basic_auth_handler, pushadd_to_gateway

registry = CollectorRegistry()

# load kubernetes configuration "in-cluster" or "out-of-cluster"
config.load_incluster_config()

HOSTNAMES = []
NAMESPACE = os.environ.get('NAMESPACE', 'network-check')
CURRENT_POD_NAME = os.environ.get('POD_NAME', 'network-check')
CURRENT_POD_NODE = os.environ.get('NODE_NAME', 'network-check')
LOGGER = logging.getLogger("network-checker")
NETWORK_LATENCY_METRIC = os.environ.get('NETWORK_LATENCY_METRIC', 'k8s_network_latency')
NETWORK_AVAILABLE_METRIC = os.environ.get('NETWORK_AVAILABLE_METRIC', 'k8s_network_available')
PROMETHEUS_PUSHGATEWAY = os.environ.get('PROMETHEUS_PUSHGATEWAY', 'https://pushgateway.adakite.ozeliurs.com')
PROMETHEUS_USERNAME = os.environ.get('PROMETHEUS_USERNAME', 'exporter')
PROMETHEUS_PASSWORD = os.environ.get('PROMETHEUS_PASSWORD', '')

# set log level
LOGGER.setLevel(logging.DEBUG)
# create console handler and set level to debug
ch = logging.StreamHandler()
ch.setLevel(logging.DEBUG)
# create formatter
formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
# add formatter to ch
ch.setFormatter(formatter)
# add ch to logger
LOGGER.addHandler(ch)

v1 = client.CoreV1Api()

network_latency = Gauge(NETWORK_LATENCY_METRIC, 'Network latency',
                        [f"from", f"to"], registry=registry)

network_available = Gauge(NETWORK_AVAILABLE_METRIC, 'Network available',
                          [f"from", f"to"], registry=registry)


# basic auth handler
def auth_handler(url, method, timeout, headers, data):
    return basic_auth_handler(url, method, timeout, headers, data, PROMETHEUS_USERNAME, PROMETHEUS_PASSWORD)


async def update_hostnames():
    while True:
        # get all pods with the specified tags
        pods = v1.list_namespaced_pod(NAMESPACE, label_selector='name=network-check').items
        hostnames = []

        for pod in pods:
            if pod.metadata.name != CURRENT_POD_NAME:
                hostnames.append(

                    {
                        "node": pod.spec.node_name,
                        "pod": pod.metadata.name,
                        "ip": pod.status.pod_ip
                    }

                )
        if hostnames != HOSTNAMES:
            print('Hostnames changed:', hostnames)
            HOSTNAMES.clear()
            HOSTNAMES.extend(hostnames)

        await asyncio.sleep(1)


async def test_connection(hostname):
    # test connection to the specified hostname
    LOGGER.info(f"Testing connection to {hostname}")
    try:
        start = time.perf_counter()
        request = requests.get(f"http://{hostname['ip']}:80", timeout=1)
        end = time.perf_counter()

        # print time that the connection took to establish
        LOGGER.info(f"Connection to {hostname} established with {request.status_code} in {(end - start) * 1000:.2f}ms")

        network_latency.labels(f"{CURRENT_POD_NODE}", f"{hostname['node']}").set((end - start) * 1000)
        network_available.labels(f"{CURRENT_POD_NODE}", f"{hostname['node']}").set(1)
    except Exception as e:
        LOGGER.error(f"Connection to {hostname} failed: {e}")
        network_latency.labels(f"{CURRENT_POD_NODE}", f"{hostname['node']}").set(0)


async def periodic():
    while True:
        await asyncio.sleep(4)
        await asyncio.gather(*[test_connection(hostname) for hostname in HOSTNAMES])
        pushadd_to_gateway(PROMETHEUS_PUSHGATEWAY, job='network-checker', registry=registry,
                           handler=auth_handler, timeout=10,
                           grouping_key={'instance': CURRENT_POD_NODE})


async def main():
    await asyncio.gather(periodic(), update_hostnames())


asyncio.run(main())
