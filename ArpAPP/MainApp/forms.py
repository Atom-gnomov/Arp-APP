from django import forms

class ArpTableForm(forms.Form):
    arp_text = forms.CharField(
        widget=forms.Textarea(attrs={'rows': 15, 'cols': 80}),
        label="Paste ARP table here"
    )