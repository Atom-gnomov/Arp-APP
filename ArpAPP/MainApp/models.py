from django.db import models

class Project(models.Model):
    Name = models.TextField(blank=True, null=True)
    Networks = models.ManyToManyField('Networks', blank=True)  # use string
    NumberOfNetworks = models.IntegerField(blank=True, null=True)
    NumberOfNodes = models.IntegerField(blank=True, null=True)


class Networks(models.Model):
    RelatedProject = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="networks")
    NetworkName = models.TextField(blank=True, null=True)
    NetworkMask = models.TextField(blank=True, null=True)
    Nodes = models.ManyToManyField('Node', blank=True)
    NumberOfNodes = models.IntegerField(blank=True, null=True)


class Node(models.Model):
    MacAddress = models.TextField()
    IpAddress = models.TextField()
    Vendor = models.TextField(blank=True, null=True)
    Type = models.TextField(blank=True, null=True)
    observable_nodes = models.ManyToManyField(
        'self',
        symmetrical=False,
        blank=True,
        related_name='observed_by'
    )




