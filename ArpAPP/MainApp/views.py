from django.db import transaction
from django.shortcuts import  get_object_or_404
from django.urls import  reverse

from django.views.generic import ListView, CreateView, DetailView

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
    template_name = 'project_network_nodes_list.html'  # set your template

    def get_queryset(self):
        project_id = self.kwargs.get('project_id')
        network_id = self.kwargs.get('network_id')

        # Make sure the project and network exist
        network = get_object_or_404(Networks, pk=network_id, RelatedProject__pk=project_id)

        # Return only nodes belonging to this network
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
        print(f"[DEBUG] GET parse-arp project={project_id} network={network_id}")
        return render(request, self.template_name, {'form': form, 'network': network, 'project_id': project_id})

    def post(self, request, project_id, network_id):
        form = ArpTableForm(request.POST)
        network = get_object_or_404(Networks, pk=network_id, RelatedProject__pk=project_id)
        logger.info("POST parse-arp for project=%s network=%s", project_id, network_id)
        print(f"[DEBUG] POST parse-arp project={project_id} network={network_id} POST keys={list(request.POST.keys())}")

        if form.is_valid():
            arp_text = form.cleaned_data.get('arp_text', '')
            logger.info("Received arp_text length=%d", len(arp_text))
            print(f"[DEBUG] arp_text (first 200 chars): {arp_text[:200]!r}")

            diag = self.parse_and_create_nodes_diagnostic(arp_text, network)

            # Log diagnostics
            logger.info("Diagnostics: %s", diag)
            print("[DEBUG] DIAG:", diag)

            return render(request, self.template_name, {
                'form': form,
                'diag': diag,
                'network': network,
                'project_id': project_id,
            })

        # invalid form
        logger.warning("Invalid form on parse-arp POST")
        print("[DEBUG] form invalid:", form.errors)
        return render(request, self.template_name, {'form': form, 'network': network, 'project_id': project_id})

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
        for raw_line in arp_text.splitlines():
            diag['lines_total'] += 1
            line = raw_line.strip()
            if not line:
                continue

            # interface detection: accept both "Interface:" and localized variants or "Интерфейс"
            iface_match = re.search(r'([0-9]{1,3}(?:\.[0-9]{1,3}){3})\s*---', line)
            if iface_match:
                current_iface = iface_match.group(1)
                diag['iface_detected_count'] += 1
                continue

            # skip header-like lines
            if re.search(r'\b(address|адрес|internet|интерфейс|physical|тип)\b', line, re.IGNORECASE):
                continue

            # try parsing IP + MAC + optional type
            arp_match = re.match(r'([0-9]{1,3}(?:\.[0-9]{1,3}){3})\s+([0-9A-Fa-f:-]{11,50})\s*(\S*)', line)
            if arp_match and current_iface:
                diag['parsed_entries_count'] += 1
                ip, mac_raw, _type = arp_match.groups()
                mac = self.normalize_mac(mac_raw)
                if len(diag['samples']) < 8:
                    diag['samples'].append({'raw': raw_line, 'ip': ip, 'mac_raw': mac_raw, 'mac_norm': mac, 'type': _type})

                if self.is_broadcast_or_multicast(mac):
                    diag['entries_skipped_broadcast'] += 1
                    continue

                try:
                    node, created = Node.objects.get_or_create(
                        MacAddress=mac,
                        defaults={'IpAddress': ip, 'Vendor': None, 'Type': None}
                    )
                    if not created and node.IpAddress != ip:
                        node.IpAddress = ip
                        node.save(update_fields=['IpAddress'])

                    attached = False
                    if not network.Nodes.filter(pk=node.pk).exists():
                        network.Nodes.add(node)
                        attached = True
                        diag['nodes_attached_count'] += 1

                    diag['nodes_created'].append({'ip': ip, 'mac': mac, 'created': bool(created), 'attached': attached})
                except Exception as e:
                    diag['errors'].append(str(e))
                    logger.exception("Error creating node")
            else:
                if len(diag['samples']) < 8:
                    diag['samples'].append({'raw_unmatched': raw_line})

        # update count
        try:
            network.NumberOfNodes = network.Nodes.count()
            network.save(update_fields=['NumberOfNodes'])
        except Exception as e:
            diag['errors'].append(str(e))
            logger.exception("Error updating network count")

        return diag