from django.shortcuts import render, redirect
from utilisateur.models import utilisateur
from Collaborateur.models import Departement , Collaborateur
from django.contrib.auth.hashers import make_password , check_password
from django.db.models import Q
from django.contrib import messages
from django.utils import timezone
from declaration_effectif.models import declaration_effectif
from django.http import JsonResponse
from datetime import date

def rec(request):
    it= request.session.get("it")
    der=declaration_effectif.objects.filter(Ru_id=it).order_by("-date").first()
    operateurs=Collaborateur.objects.filter(ru_it_id=it)
    if der :
        derniere= der.date
        changements= declaration_effectif.objects.filter(nature__in=["C","D"] ,Ru_id=it , date=derniere)
        ajouters=declaration_effectif.objects.filter(nature__in="A" ,Ru_id=it , date=derniere)
        liste_ch=set(changements.values_list("collaborateur_it_id",flat=True))
        liste_a=set(ajouters.values_list("collaborateur_it_id",flat=True))
        operateurs_finaux = Collaborateur.objects.filter(
            (Q(ru_it_id=it) & ~Q(it__in=liste_ch)) | Q(it__in=liste_a)
        )
    else:
        operateurs_finaux =operateurs

    return (operateurs_finaux)

def operateurs(request):
    operateurs_finaux = rec(request)
    return render(request, "Collaborateur/RU/liste_operateur.html", {"operateurs_finaux": operateurs_finaux})

#filter la table de validation et liste des op
def filter_tableau(request):
        text = request.GET.get('q', '')
        choix = request.GET.get('choix', '')
        choix2=request.GET.get('choix2', '')
        it = request.session.get("it")
        operateurs = rec(request)
        der = declaration_effectif.objects.filter(Ru_id=it).order_by("-date").first()
        maint = timezone.now()
        if choix and choix != "Tous les lots":
            operateurs = operateurs.filter(lot=choix)

        if text:
            operateurs = operateurs.filter(
                Q(nom_complete__icontains=text) |
                Q(matricule__icontains=text) | Q(it__icontains=text)
            )
        if choix2 and choix2 != "Tous les post":
            operateurs = operateurs.filter(post=choix2)
        

        raw = operateurs.values("matricule","it", "nom_complete","lot","post")

        data = [
            {
                "matricule": d["matricule"],
                "it":d["it"],
                "nom_complete": d["nom_complete"],
                "lot": d["lot"],
                "post": d["post"],
            }
            for d in raw
        ]
        if der and der.date == maint.date():
                return JsonResponse(data, safe=True)
        else:
                return JsonResponse(data, safe=False)

#recuperer les donnes d'un op
def operateur(request):
    it = request.GET.get('q', '').strip()

    try:
        op = Collaborateur.objects.get(it=it)
    except Collaborateur.DoesNotExist:
        return JsonResponse({"error": "Opérateur introuvable."}, status=404)
    except Collaborateur.MultipleObjectsReturned:
        return JsonResponse({"error": "Plusieurs opérateurs trouvés."}, status=409)

    if op.ru_it_id == request.session.get("it"):
        return JsonResponse({"error": "Cet opérateur appartient déjà à votre lot."}, status=400)

    data = {
        "matricule": op.matricule,
        "it":op.it,
        "nom_complete": op.nom_complete,
        "lot": op.lot,
        "post":op.post}
    return JsonResponse(data)

def liste_par_jour(request):
    jour=request.GET.get("time","")
    it=request.session.get("it")
    operateurs=declaration_effectif.objects.filter(Ru_id=it,date=jour,nature__in=["V","A"])
    raw = operateurs.values(
            "collaborateur_it__matricule",
            "collaborateur_it_id",
            "collaborateur_it__nom_complete",
            "collaborateur_it__lot",
            "collaborateur_it__post",
        )

    data = [
            {
                "matricule": d["collaborateur_it__matricule"],
                "it":d["collaborateur_it_id"],
                "nom_complete": d["collaborateur_it__nom_complete"],
                "lot": d["collaborateur_it__lot"],
                "post": d["collaborateur_it__post"],
            }
            for d in raw
        ]

    return JsonResponse(data ,safe=False)

def Ru_Rg(it):
    collab=Collaborateur.objects.filter(ru_it_id=it)
    liste=[]
    if collab:
        for c in collab:
            if Collaborateur.objects.filter(ru_it_id=c.it).count()>0:
                liste.append(c.it)
    result=Collaborateur.objects.filter(it__in=liste)
    return(result)

def liste_Ru_par_Rg(request):
    it=request.session.get("it")
    operat=Ru_Rg(it)
    n1=set(operat.values_list("it",flat=True))
    col=Collaborateur.objects.filter(ru_it_id=it).exclude(it__in=n1)
    return render(request, "Collaborateur/RG/liste_RU.html", {"operateurs": operat,"col":col})

def Rg_Dur(it):
    operateurs=Collaborateur.objects.filter(ru_it_id=it)
    liste=[]
    for c in operateurs :
        lis=Ru_Rg(c.it).count()
        if lis>0:
            liste.append(c.it)
    resultat=Collaborateur.objects.filter(it__in=liste)
    return (resultat)


def liste_N1_pr_N3(it):
    n2 = Rg_Dur(it)
    rg_ids = set(n2.values_list("it", flat=True))

    n1 = set()
    for rg in n2:
        ru_du_rg = Ru_Rg(rg.it)
        n1.update(ru_du_rg.values_list("it", flat=True))

    n1 -= rg_ids

    return n1

def liste_Rg_par_dur(request):
    it=request.session.get("it")
    operat=Rg_Dur(it)
    col=Collaborateur.objects.filter(Q(ru_it_id=it)& ~Q(it__in=set(operat.values_list("it",flat=True))))
    nbr=operat.count()
    return render(request,"Collaborateur/DUR/liste_RG.html",{"operateurs":operat,"nbr":nbr,"col":col})


def liste_N3_N4(it):
    return Collaborateur.objects.filter(ru_it_id=it)


def a_deux_niveaux(it):
    # Niveau 1 en dessous de it (ex: les N2)
    niveau_moins_1 = Collaborateur.objects.filter(ru_it_id=it)
    if not niveau_moins_1.exists():
        return False

    niveau_moins_1_ids = set(niveau_moins_1.values_list("it", flat=True))

    # Niveau 2 en dessous de it (ex: les N1)
    niveau_moins_2 = Collaborateur.objects.filter(ru_it_id__in=niveau_moins_1_ids)
    if not niveau_moins_2.exists():
        return False

    return True


def respo_N4(request):
    it = request.session.get("it")
    if not it:
        return redirect("login")

    candidats_N3 = liste_N3_N4(it)
    n3_valides_ids = [
        c.it for c in candidats_N3 if a_deux_niveaux(c.it)
    ]
    N3 = Collaborateur.objects.filter(it__in=n3_valides_ids)
    nbr = N3.count()

    return render(request, "Collaborateur/N4/liste_N3.html", {"N3": N3, "nbr": nbr})
