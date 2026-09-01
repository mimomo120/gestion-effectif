from itertools import count
from utilisateur.decorators import role_required
from django.shortcuts import render, redirect
from utilisateur.models import utilisateur
from Collaborateur.models import Departement , Collaborateur
from django.contrib.auth.hashers import make_password , check_password
from django.db.models import Q
from django.contrib import messages
from django.utils import timezone
from declaration_effectif.models import declaration_effectif
from django.http import JsonResponse
from datetime import date , datetime
from django.core.paginator import Paginator

#-------------------------------------------------------#
#Cette fct return les operateurs d'un responsable N+1
#-------------------------------------------------------#
def rec(request):
    it = request.session.get("it")

    der = (
        declaration_effectif.objects
        .filter(Q(Ru_id=it) | Q(nv_Ru_id=it, nature="C"))
        .order_by("-date")
        .first()
    )
    operateurs = SystEff(it)

    if der:
        derniere = der.date

        historique = (
            declaration_effectif.objects
            .filter(
                Q(Ru_id=it, nature__in=["C", "D", "A", "V"]) |
                Q(nv_Ru_id=it, nature="C"),
                date__lte=derniere,
            )
            .order_by("collaborateur_it_id", "-date", "-id")
        )

        dernier_etat_par_collab = {}
        for decl in historique:
            cid = decl.collaborateur_it_id
            if cid in dernier_etat_par_collab:
                continue
            if decl.nature == "C":
                etat = "inclure" if decl.nv_Ru_id == it else "exclure"
            elif decl.nature == "D":
                etat = "exclure"
            else:
                etat = "inclure"

            dernier_etat_par_collab[cid] = etat

        liste_exclure = {c for c, etat in dernier_etat_par_collab.items() if etat == "exclure"}
        liste_inclure = {c for c, etat in dernier_etat_par_collab.items() if etat == "inclure"}

        operateurs_finaux = Collaborateur.objects.filter(
            (Q(ru_it_id=it) & ~Q(it__in=liste_exclure)) | Q(it__in=liste_inclure)
        ).exclude(it=it)
    else:
        operateurs_finaux = operateurs

    return operateurs_finaux

#----------------------------------------------------------------------#
#Cette fct return les operateurs reel d'un responsable N+1  ds un jour
#----------------------------------------------------------------------#
def reelEff(it, date_reference=None):
    if not it:
        return Collaborateur.objects.none()

    qs_declarations = declaration_effectif.objects.filter(Ru_id=it)
    if date_reference:
        qs_declarations = qs_declarations.filter(date__lte=date_reference)
    der = qs_declarations.order_by("-date").first()

    changements_tous = declaration_effectif.objects.filter(nature__in=["C", "D"], Ru_id=it)
    if date_reference:
        changements_tous = changements_tous.filter(date__lte=date_reference)
    liste_ch = set(changements_tous.values_list("collaborateur_it_id", flat=True))

    if der:
        derniere = der.date
        ajouters = declaration_effectif.objects.filter(nature="A", Ru_id=it, date=derniere)
        liste_a = set(ajouters.values_list("collaborateur_it_id", flat=True))
        valider = declaration_effectif.objects.filter(nature="V", Ru_id=it, date=derniere)
        liste_v = set(valider.values_list("collaborateur_it_id", flat=True))

        base_qs = Collaborateur.objects.filter(ru_it_id=it)

        # Base : opérateurs déjà rattachés en BDD, hors ceux partis/changés
        operateurs_qs = base_qs.filter(~Q(it__in=liste_ch)).exclude(it=it)

        # Ajoutés :
        ajout_qs = Collaborateur.objects.filter(it__in=liste_a).exclude(it=it)

        # Validés :
        valide_qs = Collaborateur.objects.filter(it__in=liste_v).exclude(it=it)

        # Union des trois ensembles, dédupliquée
        operateurs_qs = (operateurs_qs | ajout_qs | valide_qs).distinct()
    else:
        operateurs_qs = Collaborateur.objects.filter(ru_it_id=it).exclude(it=it)

    try:
        ru_it_field = Collaborateur._meta.get_field("ru_it")
    except Exception:
        ru_it_field = None

    if ru_it_field is not None and getattr(ru_it_field, "is_relation", False):
        managers_qs = Collaborateur.objects.exclude(ru_it_id__isnull=True).values_list("ru_it_id", flat=True).distinct()
        operateurs_finaux = operateurs_qs.exclude(pk__in=managers_qs)
    else:
        managers_vals = Collaborateur.objects.exclude(ru_it__isnull=True).values_list("ru_it", flat=True).distinct()
        operateurs_finaux = operateurs_qs.exclude(it__in=managers_vals)

    return operateurs_finaux
#-------------------------------------------------------#
#Cette fct return les operateurs  systeme d'un responsable N+1
#-------------------------------------------------------#
def SystEff(it):
    if not it:
        return Collaborateur.objects.none()

    managers_its = (
        Collaborateur.objects
        .exclude(ru_it_id__isnull=True)
        .values_list('ru_it_id', flat=True)
        .distinct()
    )

    operateur_syst = (
        Collaborateur.objects
        .filter(ru_it_id=it)
        .exclude(it__in=managers_its)
        .exclude(it=it)
    )

    return operateur_syst

#-------------------------------------------------------#
#Cette fct render vers dashboard de responsable N+1
#-------------------------------------------------------#
@role_required('N+1')
def operateurs(request):
    it = request.session.get("it")
    operateurs_list = reelEff(it)
    count = operateurs_list.count()

    # Définition du nombre d'éléments par page
    items_per_page = 10
    paginator = Paginator(operateurs_list, items_per_page)

    # Récupération du numéro de page depuis l'URL (?page=1)
    page_number = request.GET.get('page', 1)

    try:
        operateurs_finaux = paginator.page(page_number)
    except PageNotAnInteger:
        # Si la page n'est pas un entier, afficher la première page
        operateurs_finaux = paginator.page(1)
    except EmptyPage:
        # Si la page est hors limites, afficher la dernière page
        operateurs_finaux = paginator.page(paginator.num_pages)

    context = {
        "operateurs_finaux": operateurs_finaux,
        "count": count,
        "page_obj": operateurs_finaux,
        "paginator": paginator,
    }

    return render(request, "Collaborateur/N1/liste_operateur.html", context)


#-------------------------------------------------------#
#Cette fct utiliser pr filter la table de liste des operateurs
#-------------------------------------------------------#
def filter_tableau(request):
    text = request.GET.get('q', '')
    choix = request.GET.get('choix', '')
    time_param = request.GET.get('time', '')
    page_number = request.GET.get('page', 1)
    it = request.session.get("it")

    date_reference = None
    if time_param:
        try:
            date_reference = datetime.strptime(time_param, "%Y-%m-%d").date()
        except ValueError:
            date_reference = None

    operateurs = reelEff(it, date_reference)

    if choix and choix != "Tous les lots":
        operateurs = operateurs.filter(lot=choix)

    if text:
        operateurs = operateurs.filter(
            Q(nom_complete__icontains=text) |
            Q(matricule__icontains=text) |
            Q(it__icontains=text)
        )

    raw = operateurs.values("matricule", "it", "nom_complete", "lot","departement_id")

    PER_PAGE = 10
    paginator = Paginator(list(raw), PER_PAGE)

    try:
        page_obj = paginator.page(page_number)
    except Exception:
        page_obj = paginator.page(1)

    data = {
        "results": list(page_obj.object_list),
        "page": page_obj.number,
        "num_pages": paginator.num_pages,
        "count": paginator.count,
        "has_next": page_obj.has_next(),
        "has_previous": page_obj.has_previous(),
    }

    return JsonResponse(data, safe=True)

#-------------------------------------------------------#
#Cette fct utiliser pr filter la table de liste de validation
#-------------------------------------------------------#

def filter_validation(request):
    text = request.GET.get('q', '')
    choix = request.GET.get('choix', '')
    it = request.session.get("it")
    ajoutes = request.GET.getlist('ajoutes')

    operateurs = rec(request)

    # --- Exclusion des managers, identique à validation_view ---
    direct_ids = list(
        Collaborateur.objects.filter(ru_it__it=it).exclude(it=it).values_list('it', flat=True)
    )
    manager_direct_ids = set(
        Collaborateur.objects.filter(ru_it_id__in=direct_ids)
        .values_list('ru_it_id', flat=True)
        .distinct()
    )
    manager_direct_its = set(
        Collaborateur.objects.filter(it__in=manager_direct_ids)
        .values_list('it', flat=True)
    )
    operateurs = operateurs.exclude(it__in=manager_direct_its)

    if ajoutes:
        extra = Collaborateur.objects.filter(it__in=ajoutes)
        operateurs = (operateurs | extra).distinct()

    if choix and choix != "Tous les lots":
        operateurs = operateurs.filter(lot=choix)

    if text:
        operateurs = operateurs.filter(
            Q(nom_complete__icontains=text) |
            Q(matricule__icontains=text) |
            Q(it__icontains=text)
        )

    raw = list(operateurs.values("matricule", "it", "nom_complete", "lot"))

    data = {
        "results": raw,
        "count": len(raw),
    }

    return JsonResponse(data)

#-------------------------------------------------------#
#Cette fct utiliser pr recuperer les donners d'un op lors de sont ajout ds la liste de validation
#-------------------------------------------------------#

def operateur(request):
    it = request.GET.get('q', '').strip()
    utilisateur = request.session.get("it")

    try:
        op = Collaborateur.objects.get(it=it)
    except Collaborateur.DoesNotExist:
        return JsonResponse({"error": "Opérateur introuvable."}, status=404)
    except Collaborateur.MultipleObjectsReturned:
        return JsonResponse({"error": "Plusieurs opérateurs trouvés."}, status=409)

    if it == utilisateur:
        return JsonResponse({"error": "Vous ne pouvez pas utiliser votre utilisateur."}, status=404)
    derniere_decl = (
        declaration_effectif.objects
        .filter(collaborateur_it_id=it)
        .order_by("-date", "-id")
        .first()
    )
    a_declare_depart = derniere_decl is not None and derniere_decl.nature == "D"
    if not a_declare_depart and op.ru_it_id == utilisateur:
        return JsonResponse({"error": "Cet opérateur appartient déjà à votre lot."}, status=400)

    operateur_count = Collaborateur.objects.filter(ru_it_id=it).count()
    if operateur_count > 0:
        return JsonResponse(
            {"error": "Ce n'est pas un opérateur (il est responsable d'autres collaborateurs)."},
            status=400
        )

    data = {
        "matricule": op.matricule,
        "it": op.it,
        "nom_complete": op.nom_complete,
        "lot": op.lot,
    }
    return JsonResponse(data)

#-------------------------------------------------------#
#Cette fct return liste des op pr un jour
#-------------------------------------------------------#
def liste_par_jour(request):
    jour=request.GET.get("time","")
    it=request.session.get("it")
    operateurs=declaration_effectif.objects.filter(Q(Ru_id=it,date=jour,nature__in=["V","A"])&~Q(collaborateur_it__it=it))
    raw = operateurs.values(
            "collaborateur_it__matricule",
            "collaborateur_it_id",
            "collaborateur_it__nom_complete",
            "collaborateur_it__lot"
        )

    data = [
            {
                "matricule": d["collaborateur_it__matricule"],
                "it":d["collaborateur_it_id"],
                "nom_complete": d["collaborateur_it__nom_complete"],
                "lot": d["collaborateur_it__lot"]
            }
            for d in raw
        ]

    return JsonResponse(data ,safe=False)

#-------------------------------------------------------#
#Cette fct return la liste des N+1 d'un N+2
#-------------------------------------------------------#

def Ru_Rg(it):
    collab = Collaborateur.objects.filter(ru_it_id=it).exclude(it=it)
    liste = []
    if collab:
        for c in collab:
            if Collaborateur.objects.filter(Q(ru_it_id=c.it) & ~Q(it=it)).count() > 0:
                liste.append(c.it)
    result = Collaborateur.objects.filter(it__in=liste)
    return result


#-------------------------------------------------------#
#Cette fct rederige vers templete de liste des N+1 par N+2
#-------------------------------------------------------#

def liste_N1_par_N2(request):
    it=request.session.get("it")
    operat=Ru_Rg(it)
    n1=set(operat.values_list("it",flat=True))
    col=Collaborateur.objects.filter(ru_it_id=it).exclude(it__in=n1)
    return render(request, "Collaborateur/N2/liste_N1.html", {"operateurs": operat,"col":col})

def rechercher_N1_par_N2(request):
    it = request.session.get("it")
    q = request.GET.get("q", "").strip()
    lot = request.GET.get("choix", "").strip()
    page_number = request.GET.get("page", 1)

    # Même logique que Ru_Rg, en queryset filtrable
    collab = Collaborateur.objects.filter(ru_it_id=it).exclude(it=it)
    liste = []
    for c in collab:
        if Collaborateur.objects.filter(Q(ru_it_id=c.it) & ~Q(it=it)).count() > 0:
            liste.append(c.it)

    resultat = Collaborateur.objects.filter(it__in=liste)

    if q:
        resultat = resultat.filter(
            Q(matricule__icontains=q) | Q(nom_complete__icontains=q)
        )
    if lot:
        resultat = resultat.filter(lot=lot)

    paginator = Paginator(resultat, 15)
    page_obj = paginator.get_page(page_number)

    results = [
        {
            "matricule": op.matricule,
            "it": op.it,
            "nom_complete": op.nom_complete,
            "lot": op.lot,
        }
        for op in page_obj
    ]

    return JsonResponse({
        "results": results,
        "count": paginator.count,
        "page": page_obj.number,
        "num_pages": paginator.num_pages,
        "has_previous": page_obj.has_previous(),
        "has_next": page_obj.has_next(),
    })
#-------------------------------------------------------#
#Cette fct return la liste des N+2 d'un N+3
#-------------------------------------------------------#

def Rg_Dur(it):
    operateurs=Collaborateur.objects.filter(Q(ru_it_id=it)&~Q(it=it))
    liste=[]
    for c in operateurs :
        lis=Ru_Rg(c.it).count()
        if lis>0:
            liste.append(c.it)
    resultat=Collaborateur.objects.filter(it__in=liste)
    return (resultat)

from django.core.paginator import Paginator
from django.http import JsonResponse
from django.db.models import Q

def rechercher_N2_par_N3(request):
    it = request.session.get("it")
    q = request.GET.get("q", "").strip()
    lot = request.GET.get("choix", "").strip()
    page_number = request.GET.get("page", 1)

    # Même logique que Rg_Dur, mais en queryset filtrable
    sous_it = Collaborateur.objects.filter(Q(ru_it_id=it) & ~Q(it=it))
    liste = []
    for c in sous_it:
        if Ru_Rg(c.it).count() > 0:
            liste.append(c.it)

    resultat = Collaborateur.objects.filter(it__in=liste)

    if q:
        resultat = resultat.filter(
            Q(matricule__icontains=q) | Q(nom_complete__icontains=q)
        )
    if lot:
        resultat = resultat.filter(lot=lot)

    paginator = Paginator(resultat, 15)
    page_obj = paginator.get_page(page_number)

    results = [
        {
            "matricule": op.matricule,
            "it": op.it,
            "nom_complete": op.nom_complete,
            "lot": op.lot,
        }
        for op in page_obj
    ]

    return JsonResponse({
        "results": results,
        "count": paginator.count,
        "page": page_obj.number,
        "num_pages": paginator.num_pages,
        "has_previous": page_obj.has_previous(),
        "has_next": page_obj.has_next(),
    })
#-------------------------------------------------------#
#Cette fct return la liste des N+1 pour N+3
#-------------------------------------------------------#
def liste_N1_pr_N3(it):
    n2 = Rg_Dur(it)
    rg_ids = set(n2.values_list("it", flat=True))

    n1 = set()
    for rg in n2:
        ru_du_rg = Ru_Rg(rg.it)
        n1.update(ru_du_rg.values_list("it", flat=True))

    n1 -= rg_ids

    return n1

#-------------------------------------------------------#
#Cette fct rederige vers templete de liste des N+2 par N+3
#-------------------------------------------------------#

def liste_N2_par_N3(request):
    it=request.session.get("it")
    operat=Rg_Dur(it)
    col=Collaborateur.objects.filter(Q(ru_it_id=it)& ~Q(it__in=set(operat.values_list("it",flat=True))))
    nbr=operat.count()
    return render(request,"Collaborateur/N3/liste_N2.html",{"operateurs":operat,"nbr":nbr,"col":col})

#-------------------------------------------------------#
#Cette fct return la liste des N+3 d'un N+4
#-------------------------------------------------------#


def liste_N3_N4(it):
    candidats_N3 = Collaborateur.objects.filter(Q(ru_it_id=it)&~Q(it=it))
    n3_valides_ids = [
            c.it for c in candidats_N3 if a_deux_niveaux(c.it)
        ]
    return n3_valides_ids

#-------------------------------------------------------#
# Verifie que un respo c'est un N+3
#-------------------------------------------------------#

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

#-------------------------------------------------------#
#Cette fct rederige vers templete de liste des N+3 par N+4
#-------------------------------------------------------#

def respo_N4(request):
    it = request.session.get("it")
    if not it:
        return redirect("login")
    n3_valides_ids = liste_N3_N4(it)
    N3 = Collaborateur.objects.filter(it__in=n3_valides_ids)
    nbr = N3.count()

    return render(request, "Collaborateur/N4/liste_N3.html", {"N3": N3, "nbr": nbr})

def verifier(request):
    nv = request.GET.get("q", "").strip()

    if not Collaborateur.objects.filter(it=nv).exists():
        return JsonResponse({"valide": False, "erreur": "Identifiant introuvable."})

    est_ru = Collaborateur.objects.filter(ru_it_id=nv).exists()
    if not est_ru:
        return JsonResponse({"valide": False, "erreur": "Cet identifiant n'est pas un RU."})

    return JsonResponse({"valide": True})


def rechercher_N3_par_N4(request):
    it = request.session.get("it")
    q = request.GET.get("q", "").strip()
    lot = request.GET.get("choix", "").strip()
    page_number = request.GET.get("page", 1)

    n3_valides_ids = liste_N3_N4(it)
    resultat = Collaborateur.objects.filter(it__in=n3_valides_ids)

    if q:
        resultat = resultat.filter(
            Q(matricule__icontains=q) | Q(nom_complete__icontains=q)
        )
    if lot:
        resultat = resultat.filter(lot=lot)

    paginator = Paginator(resultat, 15)
    page_obj = paginator.get_page(page_number)

    results = [
        {
            "matricule": op.matricule,
            "it": op.it,
            "nom_complete": op.nom_complete,
            "lot": op.lot,
        }
        for op in page_obj
    ]

    return JsonResponse({
        "results": results,
        "count": paginator.count,
        "page": page_obj.number,
        "num_pages": paginator.num_pages,
        "has_previous": page_obj.has_previous(),
        "has_next": page_obj.has_next(),
    })