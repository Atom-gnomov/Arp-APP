from django.shortcuts import render, get_object_or_404
from django.urls import reverse_lazy, reverse
from django.views import View
from django.views.generic import ListView, CreateView, DetailView

from MainApp.models import Project, Networks


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
