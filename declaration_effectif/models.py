from django.db import models
from Collaborateur.models import Collaborateur , Departement

# Create your models here.
class declaration_effectif(models.Model):
    collaborateur_it=models.ForeignKey(Collaborateur ,
                                        to_field="it",
                                        on_delete=models.SET_NULL,
                                        null=True,
                                        related_name="declarations_concernees")
    Ru=models.ForeignKey(Collaborateur ,
                            to_field="it",
                            on_delete=models.SET_NULL,
                                        null=True,
                            related_name="declarations_faite_par")
    date = models.DateField(auto_now_add=True)
    nature=models.CharField(max_length=1
                            ,choices=[("V","valider"),("D","depart"),("C","Changer"),("A","ajouter")])
    nv_Ru=models.ForeignKey(Collaborateur ,
                                to_field="it",
                                on_delete=models.SET_NULL,
                                related_name="declarations_vers",
                                null=True,blank=True
                                )
    def __str__(self):
        return(self.nature)
    
class historique(models.Model):
    collaborateur=models.CharField(max_length=30,null=False)
    initial=models.CharField(max_length=30,null=False)
    acceuil=models.CharField(max_length=30,null=False)
    etat=models.CharField(max_length=30,null=False)
    dpt_init=models.ForeignKey(
        Departement,
        to_field="abreviation",
        on_delete=models.SET_NULL,
        null=True,
        related_name="DPT_initial"
    )
    dpt_acceuil=models.ForeignKey(
        Departement,
        to_field="abreviation",
        on_delete=models.SET_NULL,
        null=True,
        related_name="DPT_acceuil"
    )
    def __str__(self):
        return self.initial

class Alert(models.Model):
    emetteur= models.ForeignKey(Collaborateur ,
                            to_field="it",on_delete=models.CASCADE,
                                        null=False,
                                        related_name="declaration_effectif_Alert_recepteur")
    recepteur=models.ForeignKey(Collaborateur ,
                            to_field="it",on_delete=models.CASCADE,
                                        null=False)
    contenu=models.CharField(max_length=100)
    date= models.DateField(auto_now_add=True)
    lu = models.BooleanField(default=False)
