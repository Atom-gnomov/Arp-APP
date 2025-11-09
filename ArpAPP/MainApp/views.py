import io
import math
import uuid
import random

import matplotlib
matplotlib.use('Agg')
import networkx as nx
from matplotlib import pyplot as plt
from MainApp.utils.oui import get_vendor_and_device_type
from django.views.generic import ListView, CreateView, DetailView
from django.db import transaction, models as dj_models
from django.shortcuts import render
from .forms import ArpTableForm
from .models import Node
import re
from django.shortcuts import get_object_or_404, redirect
from django.views import View
from django.core.files.base import ContentFile
from django.urls import reverse
from .models import Project, GraphImage, Networks

class ProjectView(ListView):
    model = Project
    context_object_name = 'projects'
    template_name = 'project_list.html'
    paginate_by = 16

class ProjectCreateView(CreateView):
    model = Project
    fields = ['Name']
    context_object_name = 'project'
    template_name = 'project_create.html'
    success_url = '/'

class ProjectDetailView(DetailView):
    model = Project
    context_object_name = 'project'
    template_name = "project_detail.html"

class NetworksCreateView(CreateView):
    model = Networks
    fields = ['NetworkName', 'NetworkMask']
    template_name = "network_create.html"

    def form_valid(self, form):
        # get project id from URL
        project_id = self.kwargs.get('project_id')
        project = get_object_or_404(Project, pk=project_id)
        form.instance.RelatedProject = project
        response = super().form_valid(form)


        project.NumberOfNetworks = project.networks.count()
        project.save()

        return response

    def get_success_url(self):

        project_id = self.kwargs.get('project_id')
        return reverse('MainApp:project_detail', kwargs={'pk': project_id})

class ProjectNetworksNodesListView(ListView):
    model = Node
    context_object_name = 'nodes'
    template_name = 'project_network_nodes_list.html'

    def get_queryset(self):
        project_id = self.kwargs.get('project_id')
        network_id = self.kwargs.get('network_id')


        network = get_object_or_404(Networks, pk=network_id, RelatedProject__pk=project_id)


        return network.Nodes.all()

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['project'] = get_object_or_404(Project, pk=self.kwargs.get('project_id'))
        context['network'] = get_object_or_404(Networks, pk=self.kwargs.get('network_id'))
        return context

class ProjectNetworksListView(ListView):
    model = Networks
    context_object_name = 'networks'
    template_name = "network_list.html"

    def get_queryset(self):
        project_id = self.kwargs.get('pk')
        return Networks.objects.filter(RelatedProject__pk=project_id)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['project'] = Project.objects.get(pk=self.kwargs.get('pk'))
        return context

import logging
logger = logging.getLogger(__name__)
BROADCAST_MAC = "ff:ff:ff:ff:ff:ff"

class ArpTableCreateNodesView(View):

    context_object_name = 'nodes'
    template_name = "arp_table_input.html"

    def get(self, request, project_id, network_id):
        form = ArpTableForm()
        network = get_object_or_404(Networks, pk=network_id, RelatedProject__pk=project_id)
        logger.info("GET parse-arp for project=%s network=%s", project_id, network_id)
        return render(request, self.template_name, {'form': form, 'network': network, 'project_id': project_id})

    def post(self, request, project_id, network_id):
        form = ArpTableForm(request.POST)
        network = get_object_or_404(Networks, pk=network_id, RelatedProject__pk=project_id)
        logger.info("POST parse-arp for project=%s network=%s", project_id, network_id)
        print(f"[DEBUG] POST parse-arp project={project_id} network={network_id} POST keys={list(request.POST.keys())}")

        if not form.is_valid():
            logger.warning("Invalid parse form POST: %s", form.errors)
            return render(request, self.template_name, {'form': form, 'network': network, 'project_id': project_id})

        arp_text = form.cleaned_data.get('arp_text', '')

        mode = form.cleaned_data.get('update_project_mode', 'recalc')

        logger.info("Received arp_text length=%d, mode=%s", len(arp_text), mode)
        print(f"[DEBUG] arp_text (first 200 chars): {arp_text[:200]!r} ; mode={mode!r}")

        diag = self.parse_and_create_nodes_diagnostic(arp_text, network)


        try:
            project = network.RelatedProject
            if mode == 'inc':

                added = diag.get('nodes_attached_count', 0)
                project.NumberOfNodes = (project.NumberOfNodes or 0) + added
                project.save(update_fields=['NumberOfNodes'])
                diag['project_nodes_count'] = project.NumberOfNodes
                diag['project_update_mode'] = 'incremental'
            else:

                total = project.networks.aggregate(total=dj_models.Sum('NumberOfNodes'))['total'] or 0
                project.NumberOfNodes = total
                project.save(update_fields=['NumberOfNodes'])
                diag['project_nodes_count'] = total
                diag['project_update_mode'] = 'recalc'
        except Exception as e:
            diag.setdefault('errors', []).append(f"Error updating project count: {e}")
            logger.exception("Error updating project count")

        logger.info("Diagnostics: %s", diag)
        print("[DEBUG] DIAG:", diag)

        return render(request, self.template_name, {
            'form': form,
            'diag': diag,
            'network': network,
            'project_id': project_id,
        })


    def normalize_mac(self, mac):
        mac = (mac or "").strip().lower()
        parts = re.split(r'[^0-9a-fA-F]+', mac)
        parts = [p.zfill(2) for p in parts if p != '']
        if len(parts) == 6:
            return ':'.join(parts)
        return mac

    def is_broadcast_or_multicast(self, mac):
        if not mac:
            return True
        mac = mac.lower()
        if mac == BROADCAST_MAC:
            return True
        try:
            first_octet = int(mac.split(':')[0], 16)
            return bool(first_octet & 1)
        except Exception:
            return True

    @transaction.atomic
    def parse_and_create_nodes_diagnostic(self, arp_text, network):

        diag = {
            'lines_total': 0,
            'iface_detected_count': 0,
            'parsed_entries_count': 0,
            'entries_skipped_broadcast': 0,
            'nodes_created': [],
            'nodes_attached_count': 0,
            'errors': [],
            'samples': [],
        }

        current_iface = None

        nodes_by_iface = {}

        for raw_line in arp_text.splitlines():
            diag['lines_total'] += 1
            line = raw_line.strip()
            if not line:
                continue


            iface_match = re.search(r'([0-9]{1,3}(?:\.[0-9]{1,3}){3})\s*---', line)
            if iface_match:
                current_iface = iface_match.group(1)
                diag['iface_detected_count'] += 1

                nodes_by_iface.setdefault(current_iface, [])
                continue


            if re.search(r'\b(address|адрес|internet|интерфейс|physical|тип)\b', line, re.IGNORECASE):
                continue


            arp_match = re.match(r'([0-9]{1,3}(?:\.[0-9]{1,3}){3})\s+([0-9A-Fa-f:-]{11,50})\s*(\S*)', line)
            if arp_match and current_iface:
                diag['parsed_entries_count'] += 1
                ip, mac_raw, _type = arp_match.groups()
                mac = self.normalize_mac(mac_raw)

                if len(diag['samples']) < 8:
                    diag['samples'].append(
                        {'raw': raw_line, 'ip': ip, 'mac_raw': mac_raw, 'mac_norm': mac, 'type': _type})


                if self.is_broadcast_or_multicast(mac):
                    diag['entries_skipped_broadcast'] += 1
                    continue

                try:

                    vendor, guessed_type = get_vendor_and_device_type(mac)


                    node, created = Node.objects.get_or_create(
                        MacAddress=mac,
                        defaults={'IpAddress': ip, 'Vendor': vendor, 'Type': guessed_type}
                    )


                    updated_fields = []
                    if not created:
                        if node.IpAddress != ip:
                            node.IpAddress = ip
                            updated_fields.append('IpAddress')


                        if vendor and (not node.Vendor or node.Vendor != vendor):
                            node.Vendor = vendor
                            updated_fields.append('Vendor')
                        if guessed_type and (not node.Type or node.Type != guessed_type):
                            node.Type = guessed_type
                            updated_fields.append('Type')

                        if updated_fields:
                            node.save(update_fields=updated_fields)


                    already_attached = network.Nodes.filter(pk=node.pk).exists()
                    network.Nodes.add(node)
                    attached = not already_attached
                    if attached:
                        diag['nodes_attached_count'] += 1

                    diag['nodes_created'].append({
                        'ip': ip,
                        'mac': mac,
                        'created': bool(created),
                        'attached': attached,
                        'vendor': node.Vendor,
                        'type': node.Type,
                    })
                except Exception as e:
                    diag['errors'].append(str(e))
                    logger.exception("Error creating/attaching node with vendor/type")
            else:

                if len(diag['samples']) < 8:
                    diag['samples'].append({'raw_unmatched': raw_line})


        try:
            for iface, node_list in nodes_by_iface.items():

                if len(node_list) < 2:
                    continue

                for src in node_list:
                    for dst in node_list:
                        if src.pk == dst.pk:
                            continue

                        src.observable_nodes.add(dst)

            network.NumberOfNodes = network.Nodes.count()
            network.save(update_fields=['NumberOfNodes'])
            diag['network_nodes_count'] = network.NumberOfNodes
        except Exception as e:
            diag['errors'].append(str(e))
            logger.exception("Error updating network/node observable relations")

        return diag


def build_project_graph(project: Project,
                        include_observable_edges: bool = True,
                        connect_switches_when_observable: bool = True,
                        add_virtual_edges: bool = True):

    G = nx.Graph()
    diag = {'devices': 0, 'switches': 0, 'edges': 0, 'virtual_edges_added': 0}


    networks = project.networks.all()


    switch_nodes = {}
    nodes_by_network = {}
    all_device_nodes = {}


    for net in networks:
        node_list = list(net.Nodes.all())
        nodes_by_network[net.pk] = node_list
        for node in node_list:
            all_device_nodes[node.pk] = node


    for net_pk, node_list in nodes_by_network.items():
        sw_id = f"sw_{net_pk}"
        switch_nodes[net_pk] = sw_id
        sw_label = (next((n.NetworkName for n in networks if n.pk == net_pk), None)
                    or f"Network {net_pk}")
        G.add_node(sw_id, label=sw_label, is_switch=True, network_pk=net_pk)
        diag['switches'] += 1

        for node in node_list:
            device_label = f"{node.IpAddress or ''}\n{node.MacAddress or ''}\n{node.Vendor or ''} / {node.Type or ''}"
            G.add_node(node.pk,
                       label=device_label,
                       IpAddress=node.IpAddress,
                       MacAddress=node.MacAddress,
                       Vendor=node.Vendor,
                       Type=node.Type,
                       is_switch=False,
                       django_pk=node.pk)

            if not G.has_edge(node.pk, sw_id):
                G.add_edge(node.pk, sw_id, kind='attached')
                diag['edges'] += 1


    observable_pairs = set()
    device_to_switch = {}


    for net_pk, sw_id in switch_nodes.items():
        for device in nodes_by_network.get(net_pk, []):
            device_to_switch[device.pk] = sw_id

    if include_observable_edges:
        for device in list(all_device_nodes.values()):
            for obs in device.observable_nodes.all():
                if obs.pk not in all_device_nodes:
                    continue
                a = device.pk
                b = obs.pk
                if a == b:
                    continue
                pair = tuple(sorted((a, b)))
                if pair in observable_pairs:
                    continue
                observable_pairs.add(pair)
                sw_a = device_to_switch.get(a)
                sw_b = device_to_switch.get(b)

                if sw_a and sw_b:
                    if sw_a == sw_b:
                        continue
                    else:
                        observable_pairs.add(('SWPAIR', tuple(sorted((sw_a, sw_b)))))
                        continue
                else:

                    if not G.has_edge(a, b):
                        G.add_edge(a, b, kind='observable')
                        diag['edges'] += 1


    if connect_switches_when_observable:
        seen_pairs = set()
        for u, v, d in list(G.edges(data=True)):
            if d.get('kind') == 'observable':

                def find_switch_for_device(device_pk):
                    for nbr in G.neighbors(device_pk):
                        if G.nodes[nbr].get('is_switch'):
                            return nbr
                    return None

                u_sw = find_switch_for_device(u)
                v_sw = find_switch_for_device(v)
                if u_sw and v_sw and u_sw != v_sw:
                    pair = tuple(sorted((u_sw, v_sw)))
                    if pair not in seen_pairs:
                        G.add_edge(u_sw, v_sw, kind='inter_switch')
                        seen_pairs.add(pair)
                        diag['edges'] += 1


    if add_virtual_edges and G.number_of_nodes() > 0:
        comps = list(nx.connected_components(G))
        if len(comps) > 1:
            reps = [next(iter(c)) for c in comps]
            base = reps[0]
            for rep in reps[1:]:
                if not G.has_edge(base, rep):
                    G.add_edge(base, rep, kind='virtual')
                    diag['virtual_edges_added'] += 1

    diag['devices'] = sum(1 for n in G.nodes if not G.nodes[n].get('is_switch'))
    return G, diag


class GenerateProjectGraphView(View):

    def post(self, request, project_id):
        project = get_object_or_404(Project, pk=project_id)


        G, diag = build_project_graph(project,
                                     include_observable_edges=True,
                                     connect_switches_when_observable=True,
                                     add_virtual_edges=True)

        if G.number_of_nodes() == 0:
            logger.info("GenerateProjectGraphView: no nodes for project %s", project_id)
            return redirect(reverse('MainApp:project_detail', kwargs={'pk': project.pk}))


        switch_nodes = [n for n in G.nodes if G.nodes[n].get('is_switch')]
        device_nodes = [n for n in G.nodes if not G.nodes[n].get('is_switch')]


        subG_switch = G.subgraph(switch_nodes).copy() if switch_nodes else nx.Graph()
        pos = {}
        if len(subG_switch) > 0:
            pos_switch = nx.spring_layout(subG_switch, seed=42, k=1.0)
            pos.update(pos_switch)
        elif switch_nodes:

            pos[switch_nodes[0]] = (0.0, 0.0)


        for sw in switch_nodes:

            members = [n for n in G.neighbors(sw) if not G.nodes[n].get('is_switch')]
            center = pos.get(sw, (random.uniform(-0.5, 0.5), random.uniform(-0.5, 0.5)))
            r = 0.5 + 0.15 * max(0, len(members) - 1)
            for i, device in enumerate(members):
                angle = (2 * math.pi * i) / max(1, len(members))
                x = center[0] + r * math.cos(angle)
                y = center[1] + r * math.sin(angle)
                pos[device] = (x, y)


        missing = [n for n in G.nodes() if n not in pos]
        if missing:
            pos_partial = nx.spring_layout(G.subgraph(missing), seed=43)
            pos.update(pos_partial)


        plt.figure(figsize=(14, 10))
        ax = plt.gca()
        ax.set_axis_off()


        switch_color = '#ffd166'
        device_palette = ['#06a3ff', '#33d69f', '#ff6b6b', '#ffa94d', '#b197fc', '#f9c74f']
        node_colors = []
        node_sizes = []
        labels = {}
        for n in G.nodes():
            if G.nodes[n].get('is_switch'):
                node_colors.append(switch_color)
                node_sizes.append(1600)
                labels[n] = G.nodes[n].get('label') or f"SW {G.nodes[n].get('network_pk')}"
            else:
                t = (G.nodes[n].get('Type') or 'unknown').lower()
                idx = abs(hash(t)) % len(device_palette)
                node_colors.append(device_palette[idx])
                node_sizes.append(700)
                labels[n] = G.nodes[n].get('label')


        attached_edges = [(u, v) for u, v, d in G.edges(data=True) if d.get('kind') == 'attached']
        observable_edges = [(u, v) for u, v, d in G.edges(data=True) if d.get('kind') == 'observable']
        inter_switch_edges = [(u, v) for u, v, d in G.edges(data=True) if d.get('kind') == 'inter_switch']
        virtual_edges = [(u, v) for u, v, d in G.edges(data=True) if d.get('kind') == 'virtual']

        nx.draw_networkx_nodes(G, pos, node_color=node_colors, node_size=node_sizes)
        nx.draw_networkx_labels(G, pos, labels=labels, font_size=8)

        if attached_edges:
            nx.draw_networkx_edges(G, pos, edgelist=attached_edges, width=1.2, style='solid', alpha=0.95)
        if observable_edges:
            nx.draw_networkx_edges(G, pos, edgelist=observable_edges, width=1.0, style='dashed', alpha=0.65, edge_color='orange')
        if inter_switch_edges:
            nx.draw_networkx_edges(G, pos, edgelist=inter_switch_edges, width=2.0, style='solid', alpha=0.9, edge_color='green')
        if virtual_edges:
            nx.draw_networkx_edges(G, pos, edgelist=virtual_edges, width=1.0, style='dotted', alpha=0.5, edge_color='gray')


        buf = io.BytesIO()
        plt.tight_layout()
        plt.savefig(buf, format='png', dpi=150, bbox_inches='tight')
        plt.close()
        buf.seek(0)
        image_data = buf.getvalue()


        filename = f"{uuid.uuid4().hex}.png"
        content = ContentFile(image_data, name=filename)

        graph_obj = GraphImage.objects.create(project=project)
        graph_obj.image.save(filename, content, save=True)

        logger.info("Generated star-style graph for project %s: nodes=%d, diag=%s", project.pk, G.number_of_nodes(), diag)
        return redirect(reverse('MainApp:project_detail', kwargs={'pk': project.pk}))