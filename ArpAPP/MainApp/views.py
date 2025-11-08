from django.db import transaction
from django.shortcuts import  get_object_or_404
from django.urls import  reverse
from MainApp.utils.oui import get_vendor_and_device_type
from django.views.generic import ListView, CreateView, DetailView
from django.db import transaction, models as dj_models
from django.shortcuts import render
from django.views import View
from .forms import ArpTableForm
from .models import Node
import re
from .models import Project, Networks


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
    fields = ['NetworkName', 'NetworkMask']  # leave out RelatedProject
    template_name = "network_create.html"

    def form_valid(self, form):
        # get project id from URL
        project_id = self.kwargs.get('project_id')
        project = get_object_or_404(Project, pk=project_id)
        form.instance.RelatedProject = project  # assign the ForeignKey
        response = super().form_valid(form)

        # optionally update NumberOfNetworks in Project
        project.NumberOfNetworks = project.networks.count()
        project.save()

        return response

    def get_success_url(self):
        # redirect back to project detail page
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
        # Optional form choice: 'recalc' (default) or 'inc' for incremental
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

    # --------------------
    # helpers
    # --------------------
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
        """
        Parse arp_text, create/get Node rows, attach them to `network`.
        Additionally fill Node.observable_nodes for nodes seen on the same interface.
        Returns diagnostic dict with counts and node entries.
        """
        diag = {
            'lines_total': 0,
            'iface_detected_count': 0,
            'parsed_entries_count': 0,
            'entries_skipped_broadcast': 0,
            'nodes_created': [],  # list of dicts {ip, mac, created, attached}
            'nodes_attached_count': 0,
            'errors': [],
            'samples': [],
        }

        current_iface = None
        # keep Node objects grouped by interface IP so we can mark observable relations later
        nodes_by_iface = {}

        for raw_line in arp_text.splitlines():
            diag['lines_total'] += 1
            line = raw_line.strip()
            if not line:
                continue

            # interface detection (language-independent)
            iface_match = re.search(r'([0-9]{1,3}(?:\.[0-9]{1,3}){3})\s*---', line)
            if iface_match:
                current_iface = iface_match.group(1)
                diag['iface_detected_count'] += 1
                # ensure container exists for this interface
                nodes_by_iface.setdefault(current_iface, [])
                continue

            # skip header-like lines
            if re.search(r'\b(address|адрес|internet|интерфейс|physical|тип)\b', line, re.IGNORECASE):
                continue

            # try parsing IP + MAC + optional type/flag
            arp_match = re.match(r'([0-9]{1,3}(?:\.[0-9]{1,3}){3})\s+([0-9A-Fa-f:-]{11,50})\s*(\S*)', line)
            if arp_match and current_iface:
                diag['parsed_entries_count'] += 1
                ip, mac_raw, _type = arp_match.groups()
                mac = self.normalize_mac(mac_raw)

                if len(diag['samples']) < 8:
                    diag['samples'].append(
                        {'raw': raw_line, 'ip': ip, 'mac_raw': mac_raw, 'mac_norm': mac, 'type': _type})

                # skip broadcast/multicast (default)
                if self.is_broadcast_or_multicast(mac):
                    diag['entries_skipped_broadcast'] += 1
                    continue

                try:
                    # dedupe by MAC (recommended)
                    node, created = Node.objects.get_or_create(
                        MacAddress=mac,
                        defaults={'IpAddress': ip, 'Vendor': None, 'Type': None}
                    )

                    # if exists but IP changed -> update
                    if not created and node.IpAddress != ip:
                        node.IpAddress = ip
                        node.save(update_fields=['IpAddress'])

                    # attach to network (idempotent)
                    already_attached = network.Nodes.filter(pk=node.pk).exists()
                    network.Nodes.add(node)
                    attached = not already_attached
                    if attached:
                        diag['nodes_attached_count'] += 1

                    diag['nodes_created'].append({'ip': ip, 'mac': mac, 'created': bool(created), 'attached': attached})

                    # record node object for this interface for observable relation building
                    nodes_by_iface.setdefault(current_iface, []).append(node)

                except Exception as e:
                    diag['errors'].append(str(e))
                    logger.exception("Error creating/attaching node")
            else:
                # collect some unmatched lines for diagnostics
                if len(diag['samples']) < 8:
                    diag['samples'].append({'raw_unmatched': raw_line})

        # Build observable relationships: for each interface group, mark nodes as observing each other
        try:
            for iface, node_list in nodes_by_iface.items():
                # if less than 2 nodes on this interface, nothing to link
                if len(node_list) < 2:
                    continue
                # for each node A add all other nodes B as observable
                for src in node_list:
                    for dst in node_list:
                        if src.pk == dst.pk:
                            continue
                        # Add is idempotent; avoids duplicates
                        src.observable_nodes.add(dst)
            # optional: update network.NumberOfNodes
            network.NumberOfNodes = network.Nodes.count()
            network.save(update_fields=['NumberOfNodes'])
            diag['network_nodes_count'] = network.NumberOfNodes
        except Exception as e:
            diag['errors'].append(str(e))
            logger.exception("Error updating network/node observable relations")

        return diag