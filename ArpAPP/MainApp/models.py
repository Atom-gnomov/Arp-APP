from django.db import models
import uuid
import os


class Project(models.Model):
    Name = models.TextField(blank=True, null=True)
    Networks = models.ManyToManyField('Networks', blank=True)
    NumberOfNetworks = models.IntegerField(blank=True, null=True)
    NumberOfNodes = models.IntegerField(blank=True, null=True)


class Networks(models.Model):
    RelatedProject = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="networks")
    NetworkName = models.TextField(blank=True, null=True)
    NetworkMask = models.TextField(blank=True, null=True)
    Nodes = models.ManyToManyField('Node', blank=True)
    NumberOfNodes = models.IntegerField(blank=True, null=True)


class Node(models.Model):
    MacAddress = models.TextField(blank=True, null=True)
    IpAddress = models.TextField()
    Vendor = models.TextField(blank=True, null=True)
    Type = models.TextField(blank=True, null=True)
    observable_nodes = models.ManyToManyField(
        'self',
        symmetrical=False,
        blank=True,
        related_name='observed_by'
    )



def graph_image_upload_path(instance, filename):

    ext = filename.split('.')[-1] if '.' in filename else 'png'
    name = f"{uuid.uuid4().hex}.{ext}"

    return os.path.join('graphs', f'project_{instance.project.pk}', name)


class GraphImage(models.Model):
    project = models.ForeignKey('Project', on_delete=models.CASCADE, related_name='graphs')
    image = models.ImageField(upload_to=graph_image_upload_path)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"Graph {self.pk} for project {self.project.pk} @ {self.created_at}"
