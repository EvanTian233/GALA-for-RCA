import logging
import datetime
import psutil
import os
import pandas as pd
import numpy as np
import gc
import json

from multiprocessing import Pool
from more_itertools import locate
from log import Logger
from os.path import dirname
from log_parsing import *
from alarm import *
import pdb
import re
import tqdm

import concurrent.futures


log_path = dirname(__file__) + '/log/' + str(datetime.datetime.now().strftime(
    '%Y-%m-%d')) + '_nezha.log'
logger = Logger(log_path, logging.DEBUG, __name__).getlog()


def get_logs_within_trace_map(trace_reader, log_reader, trace_id):
    """
    "Sequence": [
        "Trace": {
            "TraceID": XXXX
            "Spans": [
                {
                    SpanID:
                    ParentID:
                    Pod:
                    "events": [
                        {
                            timestamp: xxx
                            event: xxx
                        },
                        {
                            timestamp: xxx
                            event: xxx
                        }
                    ]
                }
            ]
        }
    ]
    """
    trace = {"traceid": trace_id, "spans": []}
    try:
        # find all span within a trace
        spans = trace_reader.loc[[trace_id], ['SpanID', 'ParentID',
                                              'PodName', 'StartTimeUnixNano', 'OperationName']]
        if len(spans['SpanID']) > 0:
            for span_index in range(len(spans['SpanID'])):
                span_id = spans['SpanID'][span_index]
                pod = spans['PodName'][span_index]

                # Add opration event first
                span = {"spanid": span_id,
                        "parentid": spans['ParentID'][span_index],
                        "pod": pod, "events": []}

                # record span start and end event
                start_event = log = spans['OperationName'][span_index] + "_start"
                end_event = log = spans['OperationName'][span_index] + "_end"
                startlog = {"timestamp": spans['StartTimeUnixNano'][span_index],
                            "event": log_parsing(start_event, pod=pod)}
                span["events"].append(startlog)
                endlog = {"timestamp": spans['EndTimeUnixNano'][span_index],
                          "event": log_parsing(end_event, pod=pod)}
                span["events"].append(endlog)

                try:
                    # Find all log within a span
                    logs = log_reader.loc[[span_id],
                                          ['TimeUnixNano', 'Log']]
                    if len(logs['TimeUnixNano']) > 0:
                        for log_index in range(len(logs['TimeUnixNano'])):
                            # Add log events
                            log = {"timestamp": logs['TimeUnixNano'][log_index],
                                   "event": log_parsing(log=logs['Log'][log_index], pod=pod)}
                            span["events"].append(log)
                except (Exception):
                    pass
                # sort event by event timestamp
                span['events'].sort(key=lambda k: (k.get('timestamp', 0)))
                trace["spans"].append(span)
                # sort span by span start timestamp
                trace['spans'].sort(key=lambda k: k['events'][0]['timestamp'])
    except (Exception):
        pass
    return trace


class Trace(object):
    def __init__(self, traceid):
        self.traceid = traceid
        self.spans = []

    def sort_spans(self):
        self.spans.sort(key=lambda k: (k.events[0].timestamp, 0))

    def append_spans(self, span):
        self.spans.append(span)

    def show_all_spans(self):
        print(self.traceid)
        for i in range(len(self.spans)):
            self.spans[i].show_all_events()


class Span(object):
    def __init__(self, spanid, parentid, pod):
        self.spanid = spanid
        self.parentid = parentid
        self.pod = pod
        self.events = []

    def sort_events(self):
        self.events.sort(key=lambda k: (k.timestamp, 0))

    def append_event(self, event):
        self.events.append(event)

    def new_timestamp(self):
        return self.events[len(self.events)-1].timestamp - 1

    def show_all_events(self):
        logger.info("%s,%s,%s", self.spanid, self.parentid, self.pod)
        for i in range(len(self.events)):
            self.events[i].show_event()
        logger.info("")


class Event(object):
    def __init__(self, event, pod, ns, timestamp=None, spanid=None, parentid=None):
        self.event = event
        self.pod = pod
        self.timestamp = timestamp
        self.spanid = spanid
        self.parentid = parentid
        self.ns = ns 

    def show_event(self):
        logger.info("%s, %s, %s, %s", self.timestamp, self.spanid, self.event,
                    from_id_to_template(self.event, self.ns))


class EventGraph():
    def __init__(self, log_template_miner):
        self.adjacency_list = {}
        self.node_list = set()
        self.pair_set = set()
        self.support_dict = {}
        self.log_template_miner = log_template_miner
        self.parent_event_map = {}
        self.event_pod_map = {}
        self.depth_pod_cache = {}
        self.template_cache = {}
        self.event_depth_lookup = {}

    def add_edge(self, node1, node2):
        if node1 not in self.adjacency_list.keys():
            self.adjacency_list[node1] = []
        self.adjacency_list[node1].append(node2)
        self.node_list.add(node1.event)
        self.node_list.add(node2.event)
        if node2.event not in self.parent_event_map:
            self.parent_event_map[node2.event] = node1.event
        if node2.event not in self.event_pod_map:
            self.event_pod_map[node2.event] = node2.pod

    def remove_edge(self, node1, node2):
        # print(node1.event, node2.event)
        # for span in self.adjacency_list[node1]:
        #     print(span.event)
        self.adjacency_list[node1].remove(node2)

    def print_adj_list(self):
        for key in self.adjacency_list.keys():
            print(f'node {key}: {self.adjacency_list[key]}')

    def show_graph(self):
        for key in self.adjacency_list.keys():
            logger.info("head:%s %s" %
                        (key.event, from_id_to_template(key.event, self.log_template_miner)))
            for item in self.adjacency_list[key]:
                logger.info("tail:%s %s" %
                            (item.event, from_id_to_template(item.event, self.log_template_miner)))
            logger.info("----")

    def get_deepth_pod(self, traget_event):
        if traget_event in self.depth_pod_cache:
            return self.depth_pod_cache[traget_event]

        pod = ""
        deepth = 0
        current_event = traget_event
        visited_events = set()
        if current_event in self.event_pod_map:
            pod = self.event_pod_map[current_event]

        while current_event in self.parent_event_map:
            if current_event in visited_events:
                break
            visited_events.add(current_event)
            parent_event = self.parent_event_map[current_event]
            template = self.template_cache.get(parent_event)
            if template is None:
                template = from_id_to_template(parent_event, self.log_template_miner)
                self.template_cache[parent_event] = template
            if "start" in template and "TraceID" not in template:
                deepth = deepth + 1
            current_event = parent_event

        result = (deepth, pod)
        self.depth_pod_cache[traget_event] = result
        return result

    def get_support(self):
        for key in self.adjacency_list.keys():
            for item in self.adjacency_list[key]:
                supprot_key = str(key.event) + "_" + str(item.event)
                if supprot_key not in self.pair_set:
                    self.support_dict[supprot_key] = 1
                    self.pair_set.add(supprot_key)
                else:
                    self.support_dict[supprot_key] += 1
        # logger.info("support_dict: %s" % self.support_dict)
        return self.support_dict


def safe_int_timestamp(value, default=0):
    if pd.isna(value):
        return default

    try:
        numeric_value = float(value)
    except (TypeError, ValueError):
        return default

    if not np.isfinite(numeric_value):
        return default

    return int(np.ceil(numeric_value))


def build_alarm_lookup(alarm_list):
    if isinstance(alarm_list, dict):
        return alarm_list

    alarm_lookup = {}
    for alarm_dict in alarm_list:
        pod_name = alarm_dict.get("pod")
        if pod_name:
            alarm_lookup[pod_name] = alarm_dict.get("alarm", [])
    return alarm_lookup


def iter_trace_spans(trace_reader, trace_id):
    if isinstance(trace_reader, dict):
        for span_row in trace_reader.get(trace_id, []):
            yield span_row
        return

    try:
        spans = trace_reader.loc[[trace_id], ['SpanID', 'ParentID',
                                              'PodName', 'StartTimeUnixNano', 'EndTimeUnixNano', 'OperationName']]
    except Exception:
        return

    if isinstance(spans, pd.Series):
        spans = spans.to_frame().T

    for span_row in spans.itertuples(index=False, name=None):
        yield span_row


def iter_span_logs(log_reader, span_id):
    if isinstance(log_reader, dict):
        for log_row in log_reader.get(span_id, []):
            yield log_row
        return

    if span_id not in log_reader.index:
        return

    logs = log_reader.loc[[span_id], ['TimeUnixNano', 'Log']]
    if isinstance(logs, pd.Series):
        logs = logs.to_frame().T

    for log_row in logs.itertuples(index=False, name=None):
        yield log_row


def get_events_within_trace(trace_reader, log_reader, trace_id, alarm_list, ns,log_template_miner):
    """
    func get_events_within_trace: get all metric alarm, log, span within a trace and transform to event
    :parameter
        trace_read - pd.csv_read(tracefile)
        log_reader - pd.csv_read(logfile)
        trace_id   - only one trace is processed at a time
        alarm_list - [{'pod': 'cartservice-579f59597d-wc2lz', 'alarm': [{'metric_type': 'CpuUsageRate(%)', 'alarm_flag': True}]
    :return
        trace class with all event (events in the same span was order by timestamp)
    """
    # trace = {"traceid": trace_id, "spans": []}
    trace = Trace("StartTraceId is %s" % trace_id)
    # logger.info(trace_id)
    alarm_lookup = build_alarm_lookup(alarm_list)
    try:
        span_rows = list(iter_trace_spans(trace_reader, trace_id))
        if len(span_rows) > 0:
            # process span independentlt and order by timestamp
            for span_row in span_rows:
                # span event
                span_id, parent_id, pod, span_start_time, span_end_time, operation_name = span_row
                # Add opration event first
                span = Span(span_id, parent_id, pod)
                # record span start and end event

                service = pod.rsplit('-', 1)[0]
                service = service.rsplit('-', 1)[0]

                start_event = service + " " + \
                    operation_name + " start"
                end_event = service + " " + \
                    operation_name + " end"
                start_timestamp = safe_int_timestamp(
                    span_start_time
                )
                end_timestamp = safe_int_timestamp(
                    span_end_time,
                    default=start_timestamp
                )
                if end_timestamp < start_timestamp:
                    end_timestamp = start_timestamp

                span.append_event(Event(timestamp=start_timestamp,
                                        event=log_parsing(log=start_event, pod=pod, log_template_miner=log_template_miner), pod=pod, ns=ns, spanid=span_id, parentid=parent_id))

                # log event
                try:
                    for log_timestamp, log in iter_span_logs(log_reader, span_id):
                        timestamp = safe_int_timestamp(
                            log_timestamp,
                            default=end_timestamp
                        )

                        # for log end after span
                        if timestamp - end_timestamp > 0:
                            end_timestamp = timestamp + 1

                        span.append_event(Event(timestamp=timestamp,
                                                event=log_parsing(log=log, pod=pod, log_template_miner=log_template_miner), pod=pod, ns=ns, spanid=span_id, parentid=parent_id))
                except Exception as e:
                    logger.error("Catch an exception:", e)
                    pass

                span.append_event(Event(timestamp=end_timestamp,
                                        event=log_parsing(log=end_event, pod=pod, log_template_miner=log_template_miner), pod=pod, ns=ns,spanid=span_id, parentid=parent_id))
                # alarm event
                # hipster
                if ns == "hipster":
                    if len(span.events) > 2:
                        # if len(span.events) >= 2:
                        # do not add alarm for client span
                        pod_alarms = alarm_lookup.get(span.pod, [])
                        if len(pod_alarms) > 0:
                                # logger.info(
                                #     "%s, %s", span.pod, pod_alarms)
                                for index in range(len(pod_alarms)):
                                    span.append_event(Event(timestamp=start_timestamp + index + 1,
                                                            event=log_parsing(log=pod_alarms[index]["metric_type"], pod="alarm",log_template_miner=log_template_miner), pod=pod, ns=ns,spanid=span_id, parentid=parent_id))
                                # only add alarm event for the first span group
                                # alarm_list.pop(i)
                elif ns == "ts":
                    pod_alarms = alarm_lookup.get(span.pod, [])
                    if len(pod_alarms) > 0:
                            # logger.info(
                            #     "%s, %s", span.pod, pod_alarms)
                            for index in range(len(pod_alarms)):
                                span.append_event(Event(timestamp=start_timestamp + index + 1,
                                                        event=log_parsing(log=pod_alarms[index]["metric_type"], pod="alarm",log_template_miner=log_template_miner), pod=pod, ns=ns,spanid=span_id, parentid=parent_id))
                            # only add alarm event for the first span group
                            # alarm_list.pop(i)

                # sort event by event timestamp
                span.sort_events()

                trace.append_spans(span)

            # sort span by span start timestamp
            trace.sort_spans()
    except Exception as e:
        pass
        # logger.error("Catch an exception: %s", e)

    return trace


def generate_event_chain(alarm_list, trace,log_template_miner):
    """
    func generate_event_chain:  integrate log and trace to event chain (Discarded)
    :parameter
        trace - trace including spans with all log from get_logs_within_trace
    :return
        pod_event_sequences -  e.g.,  [['frontend-579b9bff58-hfpmg_4', 'frontend-579b9bff58-hfpmg_4'], ['frontend-579b9bff58-hfpmg_4']]
        event_sequences - e.g., [[1,2,3], [2,3,4]]
    """
    event_chain = []
    pod_event_chain_list = []  # [pod_1, pod_2]
    event_chain_list = []  # [1, 2, 3]
    # trace.show_all_spans()
    for span in trace.spans:
        if span.parentid == "root":
            for event in span.events:
                event_chain.append(event)

        elif len(span.events) == 2:
            # for client span
            # find all event index of parent span
            parent_index1 = list(locate(event_chain, lambda event: event.spanid ==
                                        span.parentid and event.pod == span.pod))
            # find all event index of spans having the same parent span
            parent_index2 = list(locate(event_chain, lambda event: event.parentid ==
                                        span.parentid and event.pod == span.pod))
            parent_index = parent_index2 + parent_index1
            # reverse order
            parent_index.sort(reverse=True)
            # reverse event span to insert the late event first
            span.events.reverse()
            for event in span.events:
                for index in parent_index:
                    if event.timestamp > event_chain[index].timestamp:
                        event_chain.insert(index+1, event)
                        break

        else:
            # # find all event index of parent span
            parent_index1 = list(locate(event_chain, lambda event: event.spanid ==
                                        span.parentid))
            parent_index2 = list(locate(event_chain, lambda event: event.parentid ==
                                        span.parentid and event.pod == span.pod))
            parent_index = parent_index2 + parent_index1
            parent_index.sort(reverse=True)
            span.events.reverse()
            for event in span.events:
                for index in parent_index:
                    if event.timestamp > event_chain[index].timestamp:
                        event_chain.insert(index+1, event)
                        break

    # for index in range(1, len(event_chain)):
    #     if event_chain[index].timestamp < event_chain[index-1].timestamp:
    #         event_chain[index-1].show_event()
    #         event_chain[index].show_event()
    #         print()

    # chain event class to str: pod_event
    for index in range(len(event_chain)):
        pod_event_chain_list.append(event_chain[index].pod +
                                    "_" + str(event_chain[index].event))
        event_chain_list.append(event_chain[index].event)

    # insert alarm before the first event belonging to the pod has alarm
    if len(alarm_list) > 0:
        for pod_alarm in alarm_list:
            pod = pod_alarm['pod']
            for index in range(len(pod_event_chain_list)):
                if re.search(pod, pod_event_chain_list[index]):
                    for alarm in pod_alarm['alarm']:
                        event = log_parsing(
                            log=alarm['metric_type'], pod="alarm", log_template_miner=log_template_miner)
                        pod_event = pod + str(event)
                        pod_event_chain_list.insert(index, pod_event)
                        event_chain_list.insert(index, event)
                    break

    return pod_event_chain_list, event_chain_list


def generate_event_graph(trace, log_template_miner):
    """
    func generate_event_graph: integrate events of different span to graph
    :parameter
        trace - trace including spans with all event from get_events_within_trace
    :return
        event_graph -
    """
    event_graph = EventGraph(log_template_miner)
    # trace.show_all_spans()
    span_lookup = {span.spanid: span for span in trace.spans}
    span_depth_cache = {}

    def get_span_depth(span):
        if span.spanid in span_depth_cache:
            return span_depth_cache[span.spanid]

        depth = 0
        parent_id = span.parentid
        visited_spans = {span.spanid}
        while parent_id != "root":
            if parent_id in visited_spans:
                break
            visited_spans.add(parent_id)
            parent_span = span_lookup.get(parent_id)
            if parent_span is None:
                break
            depth += 1
            parent_id = parent_span.parentid

        span_depth_cache[span.spanid] = depth
        return depth

    for span in trace.spans:
        span_depth = get_span_depth(span)
        for index, event in enumerate(span.events):
            event_depth = span_depth + (1 if index > 0 else 0)
            current_depth = event_graph.event_depth_lookup.get(event.event)
            if current_depth is None or event_depth > current_depth[0]:
                event_graph.event_depth_lookup[event.event] = (event_depth, span.pod)

    for span in trace.spans:
        # add edge in the span group
        for index in range(1, len(span.events)):
            event_graph.add_edge(
                span.events[index-1], span.events[index])

    for span in trace.spans:
        # add relation from parent to child
        # if span.parentid != "root":
        for parent_span in trace.spans:
            if parent_span.spanid == span.parentid:
                if parent_span.pod == span.pod:
                    # if in the same pod, insert based on timestamp
                    start_timestamp = span.events[0].timestamp

                    for index in range(1, len(parent_span.events)):
                        if parent_span.events[index].timestamp > start_timestamp:
                            event_graph.add_edge(
                                parent_span.events[index-1], span.events[0])
                            break

                else:
                    # if not in the same pod, insert after the first span of parent group
                    event_graph.add_edge(
                        parent_span.events[0], span.events[0])
                break
    # event_graph.show_graph()
    # event_graph.get_support()
    # print(event_graph.support_dict)
    return event_graph


def data_integrate(trace_file, trace_id_file, log_file, alarm_list, ns,log_template_miner):
    """
    func data_integrate: integrate multimodle data to event graph
    :parameter
        trace_file
        trace_id_file
        log_file
        alarm_list
        ns
    :return
        list of event graph
    """
    logger.info(alarm_list)
    # alarm_list = []
    trace_id_reader = pd.read_csv(
        trace_id_file, index_col=False, header=None, engine='c')
    trace_reader = pd.read_csv(
        trace_file, index_col='TraceID', usecols=['TraceID', 'SpanID', 'ParentID', 'PodName', 'StartTimeUnixNano', 'EndTimeUnixNano', 'OperationName'],engine='c')
    log_reader = pd.read_csv(log_file, index_col='SpanID', usecols=[
        'TimeUnixNano', 'SpanID', 'Log'], engine='c')
    log_sequences = []
    traces = []
    # print(datetime.datetime.now())
    event_graphs = []
    # pool = Pool(200)

    # traces = [pool.apply_async(get_logs_within_trace_map, args=(
    #     trace_reader, log_reader, trace_id_reader[0][i],)) for i in range(len(trace_id_reader[0]))]

    # for test
    # trace = get_events_within_trace(trace_reader, log_reader,
    #                                 trace_id_reader[0][3], alarm_list)
    # graph = generate_event_graph(trace)
    # event_graphs.append(graph)

    with concurrent.futures.ProcessPoolExecutor(max_workers=32) as executor1:
        futures1 = {executor1.submit(
            get_events_within_trace, trace_reader, log_reader, traceid, alarm_list, ns,log_template_miner) for traceid in trace_id_reader[0]}

        for future1 in concurrent.futures.as_completed(futures1):
            trace = future1.result()
            if trace is not None:
                log_sequences.append(trace)
        executor1.shutdown()

    with concurrent.futures.ProcessPoolExecutor(max_workers=32) as executor2:
        futures2 = {executor2.submit(
            generate_event_graph, trace,log_template_miner) for trace in log_sequences}

        for future2 in concurrent.futures.as_completed(futures2):
            graph = future2.result()
            if graph is not None:
                event_graphs.append(graph)
        executor2.shutdown()

    # for running
    # traces = [pool.apply_async(get_events_within_trace, args=(
    #     trace_reader, log_reader, traceid, alarm_list, ns,)) for traceid in trace_id_reader[0]]
    # for trace in traces:
    #     log_sequences.append(trace.get())

    # graphs = [pool.apply_async(
    #     generate_event_graph, args=(trace,ns,)) for trace in log_sequences]
    # for graph in graphs:
    #     event_graphs.append(graph.get())

    for graph in event_graphs:
        # logger.info("Start show graph")
        # graph.show_graph()
        graph.get_support()
        # graph.get_deepth(107)

    # del trace_id_reader
    # del trace_reader
    # del log_reader

    # pool.close()
    # pool.join()

    # events[0][0].show_event()
    # print(datetime.datetime.now())
    logger.info("Data Integrate Complete!")
    return event_graphs


if __name__ == '__main__':
    date = "2023-01-29"
    hour_min = "08_50"
    construction_data_path = "./construct_data"
    trace_file = construction_data_path + "/" + \
        date + "/trace/" + hour_min + "_trace.csv"
    trace_id_file = construction_data_path + "/" + date + \
        "/traceid/" + hour_min + "_traceid.csv"
    log_file = construction_data_path + "/" + date + \
        "/log/" + hour_min + "_log.csv"
    
    ns = "ts"

    alarm_list = []
    event_graphs = data_integrate(
        trace_file, trace_id_file, log_file, alarm_list, ns)
    
