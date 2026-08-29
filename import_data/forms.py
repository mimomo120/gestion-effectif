from django import forms

class MultipleImportForm(forms.Form):
    fichier_departement = forms.FileField(required=False,
        label="Fichier Départements",
        widget=forms.FileInput(attrs={'class': 'form-control'})
    )
    fichier_unite = forms.FileField(required=False,
        label="Fichier Unités",
        widget=forms.FileInput(attrs={'class': 'form-control'})
    )
    fichier_collaborateur = forms.FileField(required=False,
        label="Fichier Collaborateurs",
        widget=forms.FileInput(attrs={'class': 'form-control'})
    )