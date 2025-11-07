from django.db import models

class Node(models.Model):
    MacAddress = models.TextField()
    IpAddress = models.TextField()
