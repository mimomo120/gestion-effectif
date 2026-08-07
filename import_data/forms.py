from django import forms

class ImportFileForm(forms.Form):
    fichier = forms.FileField(
        label="Fichier à importer (.csv ou .xlsx)"
    )