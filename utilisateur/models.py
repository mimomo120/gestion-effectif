from django.db import models
from django.core.validators import MinLengthValidator
from django.contrib.auth.hashers import make_password, check_password

# Create your models here.
class utilisateur(models.Model):
    password=models.CharField(max_length=277, blank=True)
    role=models.CharField(max_length=10,null=False,choices=[("N+1","N+1"),("N+2"," N+2"),("N+3","N+3"),("N+4","N+4"),("HRBP", "Human Resources Business Partner"),("ADMIN", "admin"),("SUPER","super admin")])
    it = models.OneToOneField(
        "Collaborateur.Collaborateur",
        to_field="it",
        on_delete=models.CASCADE,
        primary_key=True,
        related_name='utilisateur'
    )
    N1=models.IntegerField(choices=[("1",True),("0",False)])
    N2=models.IntegerField(choices=[("1",True),("0",False)])
    N3=models.IntegerField(choices=[("1",True),("0",False)])
    N4=models.IntegerField(choices=[("1",True),("0",False)])
    HRBP=models.IntegerField(choices=[("1",True),("0",False)])
    ADMIN=models.IntegerField(choices=[("1",True),("0",False)])
    SUPER=models.IntegerField(choices=[("1",True),("0",False)])
    doit_changer_mdp = models.BooleanField(default=False)
    def set_password(self, raw_password):
        """Hasher le password avant stockage"""
        self.password = make_password(raw_password)
        self.save()
    
    def check_password(self, raw_password):
        return check_password(raw_password, self.password)
    def __str__(self):
        return self.role
    