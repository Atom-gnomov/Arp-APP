from django.urls import path

from MainApp import views
from MainApp.views import ProjectView, ProjectCreateView, ProjectDetailView, NetworksCreateView, ProjectNetworksListView

app_name = "MainApp"

urlpatterns = [
    path('', ProjectView.as_view(), name='project_list'),
    path('create/', ProjectCreateView.as_view(), name='project_create'),
    path('project/<int:pk>/', ProjectDetailView.as_view(), name='project_detail'),
    path('project/<int:project_id>/network/create/', NetworksCreateView.as_view(), name='network_create'),
    path('project/<int:pk>/networks/', ProjectNetworksListView.as_view(), name='project_networks'),
]