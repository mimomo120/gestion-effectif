from utilisateur.decorators import role_required
from django.shortcuts import render, redirect , get_object_or_404
from utilisateur.models import utilisateur
from Collaborateur.models import (
    Departement, Collaborateur, MaquetteN1, HistoriqueMaquetteN1,
    LOT_VERS_CHAMP_MAQUETTE,
)
from django.contrib.auth.hashers import make_password , check_password
from django.db.models import Q , Exists, OuterRef, Subquery , Count ,Max ,F
from django.contrib import messages
from django.utils import timezone
from declaration_effectif.models import declaration_effectif
from django.http import JsonResponse, Http404
from datetime import date , datetime
from django.core.paginator import Paginator, EmptyPage, PageNotAnInteger
from django.core.cache import cache
from import_data.views import get_collaborateurs_reels
from django.db import transaction
from django.http import JsonResponse, HttpResponseBadRequest, HttpResponseForbidden
from django.views.decorators.http import require_POST
from calendar import monthrange
import bisect
from collections import defaultdict
CACHE_TTL = 120

LOTS_OPERATEUR_N1 = ["A", "O", "P"]

def _is_ajax(req):
    return req.headers.get("x-requested-with") == "XMLHttpRequest"


def get_managers_its():
    managers = cache.get("managers_its")
    if managers is None:
        managers = set(
            Collaborateur.objects.exclude(ru_it_id__isnull=True)
            .values_list("ru_it_id", flat=True)
            .distinct()
        )
        cache.set("managers_its", managers, CACHE_TTL)
    return managers


def get_managers_avec_operateurs_aop():

    managers = cache.get("managers_avec_aop")
    if managers is None:
        avec_aop = set(
            Collaborateur.objects.filter(lot__in=LOTS_OPERATEUR_N1)
            .exclude(ru_it_id__isnull=True)
            .exclude(ru_it_id=F('it'))
            .values_list("ru_it_id", flat=True)
            .distinct()
        )
        avec_ce = set(
            Collaborateur.objects.filter(lot__in=["C", "E"])
            .exclude(ru_it_id__isnull=True)
            .exclude(ru_it_id=F('it'))
            .values_list("ru_it_id", flat=True)
            .distinct()
        )
        managers = avec_aop - avec_ce
        cache.set("managers_avec_aop", managers, CACHE_TTL)
    return managers


def get_hierarchie_map():
    mapping = cache.get("hierarchie_map")
    if mapping is None:
        mapping = {}
        paires = Collaborateur.objects.exclude(ru_it_id__isnull=True).values_list("it", "ru_it_id")
        for it_c, ru_it_id in paires:
            if ru_it_id != it_c:
                mapping.setdefault(ru_it_id, []).append(it_c)
        cache.set("hierarchie_map", mapping, CACHE_TTL)
    return mapping


def invalider_cache_hierarchie():
    cache.delete_many([
        "managers_its", "hierarchie_map", "ru_reel_et_departs",
        "managers_avec_aop", "niveaux_hierarchie_v1",
    ])


# ------------------------------------------------------------------
# DÉFINITION CANONIQUE DU NIVEAU HIÉRARCHIQUE (N+1, N+2, N+3...)
# ------------------------------------------------------------------
def niveau_hierarchique(it, tous_ru_it=None, mapping=None, cache_niveau=None,
                        chemin_en_cours=None, managers_aop=None):

    if tous_ru_it is None:
        tous_ru_it = get_managers_its()
    if mapping is None:
        mapping = get_hierarchie_map()
    if managers_aop is None:
        managers_aop = get_managers_avec_operateurs_aop()
    if cache_niveau is None:
        cache_niveau = {}
    if chemin_en_cours is None:
        chemin_en_cours = set()

    if it in cache_niveau:
        return cache_niveau[it]

    if it in chemin_en_cours:
        raise ValueError(
            f"Boucle détectée dans la hiérarchie RU autour de '{it}' "
            f"(chemin : {' -> '.join(chemin_en_cours)} -> {it})."
        )
    chemin_en_cours = chemin_en_cours | {it}

    enfants_managers = [c for c in mapping.get(it, []) if c in tous_ru_it]

    niveaux_enfants_valides = [
        n for n in (
            niveau_hierarchique(c, tous_ru_it, mapping, cache_niveau, chemin_en_cours, managers_aop)
            for c in enfants_managers
        )
        if n >= 1
    ]

    if niveaux_enfants_valides:
        niveau = 1 + max(niveaux_enfants_valides)
    elif it in managers_aop:
        niveau = 1
    else:
        niveau = 0

    cache_niveau[it] = niveau
    return niveau


def get_maquettes_n1_map(n1_ids, departement=None, actif=True):

    n1_ids = list(n1_ids)
    if not n1_ids:
        return {}

    qs = MaquetteN1.objects.filter(n1_id__in=n1_ids)
    if actif is not None:
        qs = qs.filter(actif=actif)
    if departement is not None:
        qs = qs.filter(departement=departement)

    return {m.n1_id: m for m in qs}


def repartir_maquette_par_lot(maquettes_n1_map, lot_stats):
    for m in maquettes_n1_map.values():
        if "P" in lot_stats:
            lot_stats["P"]["maquette"] += m.P or 0
        if "E" in lot_stats:
            lot_stats["E"]["maquette"] += m.T or 0
        if "C" in lot_stats:
            lot_stats["C"]["maquette"] += m.C or 0

        val_a = getattr(m, 'A', 0) or 0
        if "A/O" in lot_stats:
            lot_stats["A/O"]["maquette"] += val_a
        elif "A" in lot_stats:
            lot_stats["A"]["maquette"] += val_a

    return lot_stats


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
    operateurs = SystEff(it).exclude(it=it)

    if der:
        derniere = der.date

        membres_du_ru = set(
            Collaborateur.objects.filter(ru_it_id=it)
            .exclude(it=it)
            .values_list("it", flat=True)
        )

        historique = (
            declaration_effectif.objects
            .filter(
                Q(collaborateur_it_id__in=membres_du_ru) |
                Q(Ru_id=it) |
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
            elif decl.nature == "A":
                etat = "inclure" if decl.Ru_id == it else "exclure"
            else: 
                etat = "inclure"

            dernier_etat_par_collab[cid] = etat

        liste_exclure = {
            c for c, etat in dernier_etat_par_collab.items() if etat == "exclure"
        }
        liste_inclure = {
            c for c, etat in dernier_etat_par_collab.items() if etat == "inclure"
        }

        operateurs_finaux = Collaborateur.objects.filter(
            (Q(ru_it_id=it) & ~Q(it__in=liste_exclure)) | Q(it__in=liste_inclure)
        ).exclude(it=it)
    else:
        operateurs_finaux = operateurs

    operateurs_finaux = operateurs_finaux.exclude(lot__in=["C", "E"])
    return operateurs_finaux


#----------------------------------------------------------------------#
#Cette fct return les operateurs reel d'un responsable N+1  ds un jour
#----------------------------------------------------------------------#
def reelEff(it, date_reference=None, managers_its=None):
    if not it:
        return Collaborateur.objects.none()

    if managers_its is None:
        managers_its = get_managers_its()

    # ------------------------------------------------------------------
    # 1) C/D sortants faits par ce RU
    # ------------------------------------------------------------------
    changements_sortants = declaration_effectif.objects.filter(
        nature__in=["C", "D"], Ru_id=it
    )
    if date_reference:
        changements_sortants = changements_sortants.filter(date__lte=date_reference)
    liste_sortis = set(
        changements_sortants.values_list("collaborateur_it_id", flat=True)
    )

    # ------------------------------------------------------------------
    # 2) Membres rattachés à ce RU
    # ------------------------------------------------------------------
    membres_du_ru = set(
        Collaborateur.objects.filter(ru_it_id=it)
        .exclude(it=it)
        .values_list("it", flat=True)
    )

    # ------------------------------------------------------------------
    # 3) Dernière déclaration (toutes natures, tous RU) de chaque membre
    #    -> pour identifier les "A faits par un autre RU"
    # ------------------------------------------------------------------
    exclus_a_autre_ru = set()
    if membres_du_ru:
        qs_decls = declaration_effectif.objects.filter(
            collaborateur_it_id__in=membres_du_ru
        )
        if date_reference:
            qs_decls = qs_decls.filter(date__lte=date_reference)

        toutes_decls = qs_decls.order_by(
            "collaborateur_it_id", "-date", "-id"
        ).values_list("collaborateur_it_id", "nature", "Ru_id")

        derniere_decl_par_collab = {}
        for cid, nat, ru_id_decl in toutes_decls:
            if cid not in derniere_decl_par_collab:
                derniere_decl_par_collab[cid] = (nat, ru_id_decl)

        exclus_a_autre_ru = {
            cid
            for cid, (nat, ru_id_decl) in derniere_decl_par_collab.items()
            if nat == "A" and ru_id_decl != it
        }

    # ------------------------------------------------------------------
    # 4) Dernière déclaration faite PAR ce RU (pour A/V à ajouter)
    # ------------------------------------------------------------------
    qs_declarations = declaration_effectif.objects.filter(Ru_id=it)
    if date_reference:
        qs_declarations = qs_declarations.filter(date__lte=date_reference)
    der = qs_declarations.order_by("-date").first()

    if der:
        derniere = der.date
        ajouters = declaration_effectif.objects.filter(
            nature="A", Ru_id=it, date=derniere
        )
        liste_a = set(ajouters.values_list("collaborateur_it_id", flat=True))

        valider = declaration_effectif.objects.filter(
            nature="V", Ru_id=it, date=derniere
        )
        liste_v = set(valider.values_list("collaborateur_it_id", flat=True))
    else:
        liste_a = set()
        liste_v = set()

    # ------------------------------------------------------------------
    # 5) Construction de la base
    # ------------------------------------------------------------------
    base_qs = Collaborateur.objects.filter(ru_it_id=it)
    operateurs_qs = (
        base_qs
        .filter(~Q(it__in=liste_sortis))
        .exclude(it__in=exclus_a_autre_ru)
        .exclude(it=it)
    )

    ajout_qs = Collaborateur.objects.filter(it__in=liste_a).exclude(it=it)
    valide_qs = Collaborateur.objects.filter(it__in=liste_v).exclude(it=it)

    operateurs_ids = set(
        (operateurs_qs | ajout_qs | valide_qs).values_list("it", flat=True)
    )

    # ------------------------------------------------------------------
    # 6) Entrants C vers ce RU
    # ------------------------------------------------------------------
    entrants_qs = declaration_effectif.objects.filter(nature="C", nv_Ru_id=it)
    if date_reference:
        entrants_qs = entrants_qs.filter(date__lte=date_reference)
    entrants_candidats = set(
        entrants_qs.values_list("collaborateur_it_id", flat=True)
    )

    if entrants_candidats:
        toutes_decls_candidats = declaration_effectif.objects.filter(
            collaborateur_it_id__in=entrants_candidats
        )
        if date_reference:
            toutes_decls_candidats = toutes_decls_candidats.filter(
                date__lte=date_reference
            )

        derniere_par_collab = {}
        for cid, dt, nat, nv_ru_id in toutes_decls_candidats.order_by(
            "collaborateur_it_id", "-date", "-id"
        ).values_list("collaborateur_it_id", "date", "nature", "nv_Ru_id"):
            derniere_par_collab.setdefault(cid, (nat, nv_ru_id))

        entrants_valides = {
            cid
            for cid, (nat, nv_ru_id) in derniere_par_collab.items()
            if nat == "C" and nv_ru_id == it
        }
        operateurs_ids |= entrants_valides

    return Collaborateur.objects.filter(it__in=operateurs_ids).exclude(it=it)
#-------------------------------------------------------#
#Cette fct return les operateurs  systeme d'un responsable N+1
#-------------------------------------------------------#
def SystEff(it, managers_its=None):
    if not it:
        return Collaborateur.objects.none()

    if managers_its is None:
        managers_its = get_managers_its()

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

    items_per_page = 10
    paginator = Paginator(operateurs_list, items_per_page)
    page_number = request.GET.get('page', 1)

    try:
        operateurs_finaux = paginator.page(page_number)
    except PageNotAnInteger:
        operateurs_finaux = paginator.page(1)
    except EmptyPage:
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

    raw = operateurs.values("matricule", "it", "nom_complete", "lot","departement_id","eq")

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
    utilisateur_it = request.session.get("it")

    try:
        op = Collaborateur.objects.get(it=it)
    except Collaborateur.DoesNotExist:
        return JsonResponse({"error": "Opérateur introuvable."}, status=404)
    except Collaborateur.MultipleObjectsReturned:
        return JsonResponse({"error": "Plusieurs opérateurs trouvés."}, status=409)

    if it == utilisateur_it:
        return JsonResponse({"error": "Vous ne pouvez pas utiliser votre utilisateur."}, status=404)
    derniere_decl = (
        declaration_effectif.objects
        .filter(collaborateur_it_id=it)
        .order_by("-date", "-id")
        .first()
    )
    a_declare_depart = derniere_decl is not None and derniere_decl.nature == "D"
    if not a_declare_depart and op.ru_it_id == utilisateur_it:
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
    tous_ru_it = get_managers_its()
    mapping = get_hierarchie_map()
    managers_aop = get_managers_avec_operateurs_aop()
    cache_niveau = {}

    n1_ids = [
        enfant for enfant in mapping.get(it, [])
        if enfant in tous_ru_it
        and niveau_hierarchique(enfant, tous_ru_it, mapping, cache_niveau, managers_aop=managers_aop) == 1
    ]
    return Collaborateur.objects.filter(it__in=n1_ids)


#-------------------------------------------------------#
# Page : liste des collaborateurs directs + RU du N2
#-------------------------------------------------------#

@role_required('N+2')
def liste_N1_par_N2(request):
    it = request.session.get("it")
    tous_les_it_reel = get_tous_les_it_sous_reel(it)
    col_qs = Collaborateur.objects.filter(it__in=tous_les_it_reel).exclude(it=it)
    PER_PAGE = 10
    
    paginator_ru = Paginator(col_qs, PER_PAGE)
    page_ru = request.GET.get("page_ru", 1)
    try:
        ru_page_obj = paginator_ru.page(page_ru)
    except PageNotAnInteger:
        ru_page_obj = paginator_ru.page(1)
    except EmptyPage:
        ru_page_obj = paginator_ru.page(paginator_ru.num_pages)
    return render(
        request,
        "Collaborateur/N2/liste_N1.html",
        {
            "operateurs": ru_page_obj,
            "ru_count": paginator_ru.count,
        },
    )


#-------------------------------------------------------#
# Endpoint AJAX : collaborateurs directs d'un RU donné
#-------------------------------------------------------#

def collaborateurs_par_ru(request, ru_it):
    it = request.session.get("it")

    ru = Ru_Rg(it).filter(it=ru_it).first()
    if not ru:
        return JsonResponse({"error": "Responsable introuvable"}, status=404)

    collaborateurs = Collaborateur.objects.filter(
        ru_it_id=ru_it
    ).exclude(it=ru_it)

    data = [
        {
            "matricule": c.matricule,
            "it": c.it,
            "nom_complete": c.nom_complete,
            "lot": c.lot,
        }
        for c in collaborateurs
    ]

    return JsonResponse({
        "ru_nom": ru.nom_complete,
        "count": len(data),
        "collaborateurs": data,
    })

def rechercher_N1_par_N2(request):

    it = request.session.get("it")
    q = request.GET.get("q", "").strip()
    lot = request.GET.get("choix", "").strip()
    page_number = request.GET.get("page", 1)

    resultat = Ru_Rg(it)

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

    tous_ru_it = get_managers_its()
    mapping = get_hierarchie_map()
    managers_aop = get_managers_avec_operateurs_aop()
    cache_niveau = {}

    n2_ids = [
        enfant for enfant in mapping.get(it, [])
        if enfant in tous_ru_it
        and niveau_hierarchique(enfant, tous_ru_it, mapping, cache_niveau, managers_aop=managers_aop) == 2
    ]
    return Collaborateur.objects.filter(it__in=n2_ids)


#-------------------------------------------------------#
#Cette fct return TOUS les 'it' sous 'it' donné, quel que soit
#le nombre de niveaux.
#-------------------------------------------------------#
def get_tous_les_it_sous(it):
    mapping = get_hierarchie_map()
    vus = set()
    a_visiter = list(mapping.get(it, []))
    while a_visiter:
        courant = a_visiter.pop()
        if courant in vus:
            continue
        vus.add(courant)
        a_visiter.extend(mapping.get(courant, []))
    return vus


#-------------------------------------------------------#
#Cette fct rederige vers templete de liste des N+2 par N+3
#-------------------------------------------------------#
def liste_N2_par_N3(request):
    it = request.session.get("it")

    tous_les_it = get_tous_les_it_sous(it)
    tous_ru_it = get_managers_its()

    tous = Collaborateur.objects.filter(it__in=tous_les_it).select_related("ru_it")

    liste_finale = [
        {
            "matricule": c.matricule,
            "it": c.it,
            "nom_complete": c.nom_complete,
            "lot": c.lot,
            "eq":c.eq,
            "est_responsable": c.it in tous_ru_it,
            "ru_nom": c.ru_it.nom_complete if c.ru_it else "-",
        }
        for c in tous
    ]

    ru_vus = {}
    for c in tous:
        if c.ru_it_id and c.ru_it_id not in ru_vus:
            ru_vus[c.ru_it_id] = c.ru_it.nom_complete if c.ru_it else c.ru_it_id
    ru_options = sorted(ru_vus.items(), key=lambda item: item[1] or "")

    return render(request, "Collaborateur/N3/liste_N2.html", {
        "operateurs": liste_finale,
        "count": len(liste_finale),
        "ru_options": ru_options,
    })


#-------------------------------------------------------#
#Endpoint AJAX : recherche + filtre lot + filtre rôle,
#sur TOUS les niveaux sous le N+3 connecté, avec le RU de chacun
#-------------------------------------------------------#
def rechercher_N2_par_N3(request):
    it = request.session.get("it")
    q = request.GET.get("q", "").strip()
    lot = request.GET.get("choix", "").strip()
    role = request.GET.get("role", "").strip()
    ru_it = request.GET.get("ru_it", "").strip()
    page_number = request.GET.get("page", 1)

    tous_les_it = get_tous_les_it_sous(it)
    tous_ru_it = get_managers_its()

    resultat = Collaborateur.objects.filter(it__in=tous_les_it).select_related("ru_it")

    if q:
        resultat = resultat.filter(
            Q(matricule__icontains=q) | Q(nom_complete__icontains=q)
        )
    if lot:
        resultat = resultat.filter(lot=lot)
    if ru_it:
        resultat = resultat.filter(ru_it_id=ru_it)

    if role == "responsable":
        resultat = resultat.filter(it__in=tous_ru_it)
    elif role == "operateur":
        resultat = resultat.exclude(it__in=tous_ru_it)

    resultat = resultat.order_by("matricule")

    paginator = Paginator(resultat, 15)
    page_obj = paginator.get_page(page_number)

    results = [
        {
            "matricule": op.matricule,
            "it": op.it,
            "nom_complete": op.nom_complete,
            "lot": op.lot, "eq":op.eq,
            "est_responsable": op.it in tous_ru_it,
            "ru_nom": op.ru_it.nom_complete if op.ru_it else "-",
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
    tous_ru_it = get_managers_its()
    mapping = get_hierarchie_map()
    managers_aop = get_managers_avec_operateurs_aop()
    cache_niveau = {}

    descendants = get_tous_les_it_sous(it)
    return {
        d for d in descendants
        if d in tous_ru_it
        and niveau_hierarchique(d, tous_ru_it, mapping, cache_niveau, managers_aop=managers_aop) == 1
    }


#-------------------------------------------------------#
#Cette fct return la liste des N+3 d'un N+4
#-------------------------------------------------------#
def liste_N3_N4(it):
    mapping = get_hierarchie_map()
    candidats = mapping.get(it, [])
    return [c for c in candidats if a_deux_niveaux(c)]

#-------------------------------------------------------#
# Verifie que un respo c'est un N+3
#-------------------------------------------------------#

def a_deux_niveaux(it):
    mapping = get_hierarchie_map()
    enfants = mapping.get(it, [])
    if not enfants:
        return False
    return any(mapping.get(e) for e in enfants)


#-------------------------------------------------------#
#Cette fct return TOUS les "vrais" N+1 sous 'it', peu importe
#leur profondeur.
#-------------------------------------------------------#
def get_tous_les_n1(it, tous_ru_it=None, managers_aop=None):
    if tous_ru_it is None:
        tous_ru_it = get_managers_its()
    if managers_aop is None:
        managers_aop = get_managers_avec_operateurs_aop()
    mapping = get_hierarchie_map()
    resultat = set()

    def _recurse(noeud):
        for enfant in mapping.get(noeud, []):
            if enfant not in tous_ru_it:
                continue
            sous_managers = [c for c in mapping.get(enfant, []) if c in tous_ru_it]
            if sous_managers:
                _recurse(enfant)
            elif enfant in managers_aop:
                resultat.add(enfant)

    _recurse(it)
    return resultat


def get_n1_et_n2_sous(it, tous_ru_it=None, managers_aop=None):
    if tous_ru_it is None:
        tous_ru_it = get_managers_its()
    if managers_aop is None:
        managers_aop = get_managers_avec_operateurs_aop()
    mapping = get_hierarchie_map()
    n1_ids = set()
    n2_ids = set()

    def _recurse(noeud):
        for enfant in mapping.get(noeud, []):
            if enfant not in tous_ru_it:
                continue
            sous_managers = [c for c in mapping.get(enfant, []) if c in tous_ru_it]
            if sous_managers:
                n2_ids.add(enfant)
                _recurse(enfant)
            elif enfant in managers_aop:
                n1_ids.add(enfant)

    _recurse(it)
    return n1_ids, n2_ids



def get_dernieres_declarations():
    derniers_ids = (
        declaration_effectif.objects
        .values("collaborateur_it")
        .annotate(dernier_id=Max("id"))
        .values_list("dernier_id", flat=True)
    )
    declarations = (
        declaration_effectif.objects
        .filter(id__in=derniers_ids)
        .select_related("collaborateur_it", "Ru", "nv_Ru")
    )
    return {d.collaborateur_it_id: d for d in declarations if d.collaborateur_it_id}


def get_ru_reel_et_departs():

    cached = cache.get("ru_reel_et_departs")
    if cached is not None:
        return cached

    dernieres = get_dernieres_declarations()
    ru_reel = {}
    departs = set()

    for it_collab, decl in dernieres.items():
        if decl.nature == "D":
            departs.add(it_collab)
        elif decl.nature == "C" and decl.nv_Ru_id:
            ru_reel[it_collab] = decl.nv_Ru_id
        elif decl.Ru_id:
            ru_reel[it_collab] = decl.Ru_id

    resultat = (ru_reel, departs)
    cache.set("ru_reel_et_departs", resultat, CACHE_TTL)
    return resultat


def get_tous_les_it_sous_reel(it):
    ru_reel, departs = get_ru_reel_et_departs()

    enfants_par_ru = {}
    for c in Collaborateur.objects.exclude(it__in=departs):
        ru_effectif = ru_reel.get(c.it, c.ru_it_id)
        if ru_effectif:
            enfants_par_ru.setdefault(ru_effectif, []).append(c.it)

    def _recurse(it_courant, vus):
        niveau = set(enfants_par_ru.get(it_courant, [])) - vus
        if not niveau:
            return set()
        vus |= niveau
        tous = set(niveau)
        for sous_it in niveau:
            tous |= _recurse(sous_it, vus)
        return tous

    return _recurse(it, set())


def reelEff_evolution_multi(ru_ids, dates):
    ru_ids = list(ru_ids)
    ru_ids_set = set(ru_ids)
    dates = sorted(set(dates))
    if not ru_ids or not dates:
        return {ru_id: {} for ru_id in ru_ids}

    base_par_ru = defaultdict(set)
    for c_it, c_ru_it in (
        Collaborateur.objects.filter(ru_it_id__in=ru_ids)
        .exclude(it=F('ru_it_id'))
        .values_list('it', 'ru_it_id')
    ):
        base_par_ru[c_ru_it].add(c_it)

    tous_membres = set()
    for s in base_par_ru.values():
        tous_membres |= s

    declarations_membres = defaultdict(list)
    if tous_membres:
        for cid, dt, nat, ru_id_decl in (
            declaration_effectif.objects
            .filter(collaborateur_it_id__in=tous_membres)
            .order_by("collaborateur_it_id", "date")
            .values_list("collaborateur_it_id", "date", "nature", "Ru_id")
        ):
            declarations_membres[cid].append((dt, nat, ru_id_decl))

    rows = list(
        declaration_effectif.objects
        .filter(Ru_id__in=ru_ids, date__lte=dates[-1], nature__in=["D", "C", "A", "V"])
        .order_by('Ru_id', 'date')
        .values_list('Ru_id', 'date', 'nature', 'collaborateur_it_id')
    )
    par_ru_rows = defaultdict(list)
    for ru_id, dt, nat, cid in rows:
        par_ru_rows[ru_id].append((dt, nat, cid))

    entrants_rows = list(
        declaration_effectif.objects
        .filter(nature="C", nv_Ru_id__in=ru_ids, date__lte=dates[-1])
        .values_list('collaborateur_it_id', flat=True)
    )
    entrants_candidats = set(entrants_rows)
    toutes_decls_par_candidat = defaultdict(list)
    if entrants_candidats:
        for cid, dt, nat, nv_ru_id in (
            declaration_effectif.objects
            .filter(collaborateur_it_id__in=entrants_candidats, date__lte=dates[-1])
            .order_by('collaborateur_it_id', 'date')
            .values_list('collaborateur_it_id', 'date', 'nature', 'nv_Ru_id')
        ):
            toutes_decls_par_candidat[cid].append((dt, nat, nv_ru_id))

    entrants_actifs_par_date = {d: defaultdict(set) for d in dates}
    for cid, decls in toutes_decls_par_candidat.items():
        dts = [x[0] for x in decls]
        for d in dates:
            p = bisect.bisect_right(dts, d) - 1
            if p < 0:
                continue
            _, nat, nv_ru_id = decls[p]
            if nat == "C" and nv_ru_id in ru_ids_set:
                entrants_actifs_par_date[d][nv_ru_id].add(cid)

    resultat = {ru_id: {} for ru_id in ru_ids}

    for ru_id in ru_ids:
        rows_ru = par_ru_rows.get(ru_id, [])
        toutes_dates_decl = sorted({r[0] for r in rows_ru})
        cd_events = sorted((r[0], r[2]) for r in rows_ru if r[1] in ("C", "D"))
        cd_dates_sorted = [e[0] for e in cd_events]

        av_par_date = defaultdict(lambda: {"A": set(), "V": set()})
        for dt, nat, cid in rows_ru:
            if nat in ("A", "V"):
                av_par_date[dt][nat].add(cid)

        base_ids = base_par_ru.get(ru_id, set())

        for d in dates:
            pos = bisect.bisect_right(toutes_dates_decl, d) - 1
            if pos < 0:
                operateurs = set(base_ids)
            else:
                derniere_d = toutes_dates_decl[pos]
                exclus_idx = bisect.bisect_right(cd_dates_sorted, d)
                exclus = {cid for (dt, cid) in cd_events[:exclus_idx]}
                av = av_par_date.get(derniere_d, {"A": set(), "V": set()})
                operateurs = (base_ids - exclus) | av["A"] | av["V"]


            exclus_date = set()
            for cid in base_ids:
                decls_cid = declarations_membres.get(cid, [])
                dts_cid = [x[0] for x in decls_cid]
                p_cid = bisect.bisect_right(dts_cid, d) - 1
                if p_cid < 0:
                    continue
                _, nat, ru_id_decl = decls_cid[p_cid]
                if nat == "A" and ru_id_decl != ru_id:
                    exclus_date.add(cid)

            operateurs -= exclus_date

            operateurs |= entrants_actifs_par_date[d].get(ru_id, set())
            operateurs.discard(ru_id)
            resultat[ru_id][d] = operateurs

    return resultat

def respo_N4(request):
    it = request.session.get("it")
    if not it:
        return redirect("login")

    tous_les_it = get_tous_les_it_sous_reel(it)
    tous_ru_it = get_managers_its()

    N3_qs = Collaborateur.objects.filter(it__in=tous_les_it).order_by("nom_complete")
    nbr = N3_qs.count()

    ru_it_ids = (
        N3_qs.exclude(ru_it_id__isnull=True)
        .values_list("ru_it_id", flat=True)
        .distinct()
    )
    responsables = Collaborateur.objects.filter(it__in=ru_it_ids).values("it", "nom_complete")
    paginator = Paginator(N3_qs, 10)
    page_obj = paginator.get_page(1)

    ru_ids_page = [c.ru_it_id for c in page_obj.object_list if c.ru_it_id]
    noms_par_it = dict(
        Collaborateur.objects.filter(it__in=ru_ids_page).values_list("it", "nom_complete")
    )

    N3 = [
        {
            "matricule": c.matricule,
            "it": c.it,
            "nom_complete": c.nom_complete,
            "lot": c.lot,
            "eq": c.eq,
            "est_responsable": c.it in tous_ru_it,
            "ru_nom": noms_par_it.get(c.ru_it_id, "-"),
        }
        for c in page_obj.object_list
    ]

    return render(request, "Collaborateur/N4/liste_N3.html", {
        "N3": N3,
        "nbr": nbr,
        "responsables": responsables,
    })

def rechercher_N3_par_N4(request):

    it = request.session.get("it")
    if not it:
        return JsonResponse({"error": "unauthorized"}, status=401)

    q = request.GET.get("q", "").strip()
    lot = request.GET.get("choix", "").strip()
    ru_it = request.GET.get("ru_it", "").strip()
    page_number = request.GET.get("page", 1)

    tous_les_it = get_tous_les_it_sous_reel(it)
    queryset = Collaborateur.objects.filter(it__in=tous_les_it)

    if q:
        queryset = queryset.filter(
            Q(matricule__icontains=q) | Q(nom_complete__icontains=q)
        )
    if lot:
        queryset = queryset.filter(lot=lot)
    if ru_it:
        queryset = queryset.filter(ru_it_id=ru_it)

    tous_ru_it = get_managers_its()

    paginator = Paginator(queryset.order_by("nom_complete"), 10)
    page_obj = paginator.get_page(page_number)

    ru_ids_page = [c.ru_it_id for c in page_obj.object_list if c.ru_it_id]
    noms_par_it = dict(
        Collaborateur.objects.filter(it__in=ru_ids_page).values_list("it", "nom_complete")
    )

    results = [
        {
            "matricule": c.matricule,
            "it": c.it,
            "nom_complete": c.nom_complete,
            "lot": c.lot,
            "eq": c.eq,
            "est_responsable": c.it in tous_ru_it,
            "ru_nom": noms_par_it.get(c.ru_it_id, "-"),
        }
        for c in page_obj.object_list
    ]

    return JsonResponse({
        "results": results,
        "count": paginator.count,
        "page": page_obj.number,
        "num_pages": paginator.num_pages,
        "has_previous": page_obj.has_previous(),
        "has_next": page_obj.has_next(),
    })


def verifier(request):
    nv = request.GET.get("q", "").strip()

    if not Collaborateur.objects.filter(it=nv).exists():
        return JsonResponse({"valide": False, "erreur": "Identifiant introuvable."})

    est_ru = Collaborateur.objects.filter(
        ru_it_id=nv, lot__in=LOTS_OPERATEUR_N1
    ).exists()
    if not est_ru:
        return JsonResponse({"valide": False, "erreur": "Cet identifiant n'est pas un RU."})

    return JsonResponse({"valide": True})

def get_effectif_reel_ids(departement_ids, at_date, ru_id=None):

    derniere_decl_id = (
        declaration_effectif.objects
        .filter(collaborateur_it=OuterRef('collaborateur_it'), date__lte=at_date)
        .order_by('-date', '-id')
        .values('id')[:1]
    )
    dernieres_declarations = (
        declaration_effectif.objects
        .filter(date__lte=at_date)
        .annotate(latest_id=Subquery(derniere_decl_id))
        .filter(id=F('latest_id'))
    )

    # Départ
    exclu1_ids = set(
        dernieres_declarations
        .filter(collaborateur_it__departement_id__in=departement_ids, nature="D")
        .values_list("collaborateur_it_id", flat=True)
    )

    # Changement vers un dept hors périmètre
    exclu2_ids = set(
        dernieres_declarations
        .filter(collaborateur_it__departement_id__in=departement_ids, nature="C")
        .exclude(nv_Ru__departement_id__in=departement_ids)
        .values_list("collaborateur_it_id", flat=True)
    )

    # Ajout/validation faite par un RU HORS du périmètre -> sort du dept d'origine
    exclu3_ids = set(
        dernieres_declarations
        .filter(collaborateur_it__departement_id__in=departement_ids, nature__in=["A", "V"])
        .exclude(Ru__departement_id__in=departement_ids)
        .values_list("collaborateur_it_id", flat=True)
    )

    # Ajout fait par un RU différent de l'utilisateur connecté
    exclu4_ids = set()
    if ru_id is not None:
        # Récupérer la liste des managers (RU) pour ne pas les exclure
        managers_its = get_managers_its()
        
        exclu4_ids = set(
            dernieres_declarations
            .filter(
                collaborateur_it__departement_id__in=departement_ids,
                nature="A"
            )
            .exclude(Ru_id=ru_id)
            .exclude(collaborateur_it_id__in=managers_its)
            .values_list("collaborateur_it_id", flat=True)
        )

    # Changement entrant dans le périmètre
    inclu_c_ids = set(
        dernieres_declarations
        .filter(nv_Ru__departement_id__in=departement_ids, nature="C")
        .values_list("collaborateur_it_id", flat=True)
    )

    if ru_id is not None:
        inclu_av_ids = set(
            dernieres_declarations
            .filter(
                Ru__departement_id__in=departement_ids,
                nature="A",
                Ru_id=ru_id
            )
            .values_list("collaborateur_it_id", flat=True)
        ) | set(
            dernieres_declarations
            .filter(
                Ru__departement_id__in=departement_ids,
                nature="V"
            )
            .values_list("collaborateur_it_id", flat=True)
        )
    else:
        inclu_av_ids = set(
            dernieres_declarations
            .filter(Ru__departement_id__in=departement_ids, nature__in=["A", "V"])
            .values_list("collaborateur_it_id", flat=True)
        )

    ids_departement = set(
        Collaborateur.objects.filter(departement_id__in=departement_ids).values_list("it", flat=True)
    )
    
    managers_its = get_managers_its()
    managers_dans_perimetre = ids_departement & managers_its
    
    return (
        (ids_departement - exclu1_ids - exclu2_ids - exclu3_ids - exclu4_ids) 
        | inclu_c_ids 
        | inclu_av_ids
        | managers_dans_perimetre
    )

def get_effectif_reel_ids_multi(departement_ids, dates):
    dates = sorted(set(dates))
    if not dates:
        return {}

    departement_ids = list(departement_ids)

    ids_departement = set(
        Collaborateur.objects.filter(departement_id__in=departement_ids)
        .values_list("it", flat=True)
    )

    rows = list(
        declaration_effectif.objects
        .filter(date__lte=dates[-1])
        .order_by("collaborateur_it_id", "date", "id")
        .values_list("collaborateur_it_id", "date", "nature", "Ru_id", "nv_Ru_id")
    )

    par_collab = defaultdict(list)
    for cid, dt, nat, ru_id, nv_ru_id in rows:
        par_collab[cid].append((dt, nat, ru_id, nv_ru_id))

    tous_ru_ids = {ru_id for _, _, _, ru_id, _ in rows if ru_id} | {nv_ru_id for _, _, _, _, nv_ru_id in rows if nv_ru_id}
    dept_par_ru = dict(
        Collaborateur.objects.filter(it__in=tous_ru_ids).values_list("it", "departement_id")
    )

    resultat = {}
    for d in dates:
        exclu_ids = set()
        inclu_ids = set()

        for cid, decls in par_collab.items():
            dts = [x[0] for x in decls]
            pos = bisect.bisect_right(dts, d) - 1
            if pos < 0:
                continue
            _, nat, ru_id, nv_ru_id = decls[pos]

            if nat == "D":
                if cid in ids_departement:
                    exclu_ids.add(cid)
            elif nat == "C":
                nv_ru_ok = dept_par_ru.get(nv_ru_id) in departement_ids
                if cid in ids_departement and not nv_ru_ok:
                    exclu_ids.add(cid)
                if nv_ru_ok:
                    inclu_ids.add(cid)
            elif nat in ("A", "V"):
                ru_ok = dept_par_ru.get(ru_id) in departement_ids
                if cid in ids_departement and not ru_ok:
                    exclu_ids.add(cid)
                if ru_ok:
                    inclu_ids.add(cid)

        resultat[d] = (ids_departement - exclu_ids) | inclu_ids

    return resultat


PER_PAGE = 20


def _parse_date(date_str):
    if not date_str:
        return timezone.now().date()
    try:
        return datetime.strptime(date_str, "%Y-%m-%d").date()
    except ValueError:
        return timezone.now().date()


FIELD_BY_ROLE = {
    "HRBP": "HRBP_id",
    "DRH": "DRH_id",
    "ADMIN": "ADMIN_id",
    "PILOT": "PILOT_id",
}


def get_departement_ids_for_role(role, it):
    field = FIELD_BY_ROLE.get(role)
    if not field:
        return []
    return list(Departement.objects.filter(**{field: it}).values_list("id", flat=True))

def get_ru_reel_par_collab(collab_ids, at_date=None):
    collab_ids = list(collab_ids)
    if not collab_ids:
        return {}

    qs = declaration_effectif.objects.filter(
        collaborateur_it_id__in=collab_ids
    )
    if at_date:
        qs = qs.filter(date__lte=at_date)

    rows = (
        qs.order_by("collaborateur_it_id", "-date", "-id")
        .values_list("collaborateur_it_id", "nature", "Ru_id", "nv_Ru_id")
    )

    resultat = {}
    for cid, nat, ru_id, nv_ru_id in rows:
        if cid in resultat:
            continue
        if nat == "C":
            resultat[cid] = nv_ru_id
        elif nat in ("A", "V"):
            resultat[cid] = ru_id
        elif nat == "D":
            resultat[cid] = None
    return resultat

def collaborateur(request):
    it = request.session.get("it")
    role = request.session.get("role")
    departement_ids = get_departement_ids_for_role(role, it)
    today = timezone.now().date()

    abreviations = list(
        Departement.objects.filter(id__in=departement_ids)
        .values_list("abreviation", flat=True)
    )
    ids_total_r = get_effectif_reel_ids(abreviations, today)
    total_r = len(ids_total_r)

    collaborateurs_qs = (
        Collaborateur.objects
        .filter(it__in=ids_total_r)
        .order_by("matricule")
    )

    # Récupérer les IDs uniques des RU
    ru_ids = (
        Collaborateur.objects
        .filter(it__in=ids_total_r, ru_it__isnull=False)
        .values_list("ru_it_id", flat=True)
        .distinct()
    )

    # Dédupliquer PAR NOM
    noms_vus = set()
    ru_choices = []
    for nom in (
        Collaborateur.objects
        .filter(it__in=ru_ids)
        .values_list("nom_complete", flat=True)
        .order_by("nom_complete")
    ):
        if nom and nom not in noms_vus:
            noms_vus.add(nom)
            ru_choices.append(nom)
    if role in ("HRBP", "ADMIN", "DRH"):
        departements_liste = list(
            Departement.objects.filter(id__in=departement_ids)
            .values_list("abreviation", flat=True)
            .order_by("abreviation")
        )
        afficher_filtre_dpt = len(departements_liste) > 1
    else:
        departements_liste = []
        afficher_filtre_dpt = False

    paginator = Paginator(collaborateurs_qs, PER_PAGE)
    collaborateur_page = paginator.page(1)

    return render(
        request,
        "declaration_effectif/PILOT/collaborateurs.html",
        {
            "collaborateur": collaborateur_page,
            "total_r": total_r,
            "today": today.isoformat(),
            "ru_choices": ru_choices,
            "departements_liste": departements_liste,
            "afficher_filtre_dpt": afficher_filtre_dpt,
            "role_actuel": role,
        },
    )

def collaborateur_api(request):
    it = request.session.get("it")
    role = request.session.get("role")

    departement_ids = get_departement_ids_for_role(role, it)
    if not departement_ids:
        raise Http404("Aucun département trouvé pour cet utilisateur.")

    search = request.GET.get("q", "").strip()
    lot = request.GET.get("lot", "").strip()
    ru = request.GET.get("ru", "").strip()
    dpt = request.GET.get("dpt", "").strip()   # ⚡ NOUVEAU
    selected_date = _parse_date(request.GET.get("date"))
    page_number = request.GET.get("page", 1)

    abreviations = list(
        Departement.objects.filter(id__in=departement_ids)
        .values_list("abreviation", flat=True)
    )

    # Uniquement autorisé pour HRBP / ADMIN / DRH
    if dpt and role in ("HRBP", "ADMIN", "DRH") and dpt in abreviations:
        abreviations_filtrees = [dpt]
    else:
        abreviations_filtrees = abreviations

    ids_total_r = get_effectif_reel_ids(abreviations_filtrees, selected_date)
    qs = Collaborateur.objects.filter(it__in=ids_total_r)

    if search:
        qs = qs.filter(
            Q(matricule__icontains=search) | Q(nom_complete__icontains=search)
        )
    if lot:
        qs = qs.filter(lot=lot)

    qs = qs.select_related("departement", "ru_it").order_by("matricule")

    # ------------------------------------------------------------------
    # Filtre par RU (nom) : sur le RU RÉEL
    # ------------------------------------------------------------------
    if ru:
        all_ids = list(qs.values_list("it", flat=True))
        ru_reel_all = get_ru_reel_par_collab(all_ids, at_date=selected_date)

        ru_it_reels = {r for r in ru_reel_all.values() if r}
        ru_it_systeme = set(
            Collaborateur.objects
            .filter(it__in=all_ids, ru_it__isnull=False)
            .values_list("ru_it_id", flat=True)
        )
        tous_ru_ids = ru_it_reels | ru_it_systeme

        noms_ru = dict(
            Collaborateur.objects.filter(it__in=tous_ru_ids)
            .values_list("it", "nom_complete")
        )

        ids_filtres = []
        for c in qs:
            ru_reel_it = ru_reel_all.get(c.it, c.ru_it_id)
            nom_ru_reel = noms_ru.get(ru_reel_it)
            if nom_ru_reel == ru:
                ids_filtres.append(c.it)

        qs = Collaborateur.objects.filter(it__in=ids_filtres).order_by("matricule")

    paginator = Paginator(qs, PER_PAGE)
    try:
        page_obj = paginator.page(page_number)
    except PageNotAnInteger:
        page_obj = paginator.page(1)
    except EmptyPage:
        page_obj = paginator.page(paginator.num_pages) if paginator.num_pages else paginator.page(1)

    # ------------------------------------------------------------------
    # RU réel de chaque collaborateur de la page
    # ------------------------------------------------------------------
    collab_ids_page = [c.it for c in page_obj]
    ru_reel_map = get_ru_reel_par_collab(collab_ids_page, at_date=selected_date)

    ru_ids_reels = {r for r in ru_reel_map.values() if r}
    ru_ids_systeme = {c.ru_it_id for c in page_obj if c.ru_it_id}
    tous_ru_ids = ru_ids_reels | ru_ids_systeme

    ru_map = {
        r.it: r for r in Collaborateur.objects.filter(it__in=tous_ru_ids)
    }

    results = []
    for c in page_obj:
        ru_reel_it = ru_reel_map.get(c.it, c.ru_it_id)
        ru_reel_obj = ru_map.get(ru_reel_it) if ru_reel_it else None

        ru_systeme_it = c.ru_it_id
        ru_change_non_declare = (
            ru_reel_it is not None
            and ru_systeme_it is not None
            and ru_reel_it != ru_systeme_it
        )

        results.append({
            "matricule": c.matricule,
            "it": c.it,
            "nom_complete": c.nom_complete,
            "lot": c.lot,
            "ru_it": ru_reel_it or "",
            "ru_nom": ru_reel_obj.nom_complete if ru_reel_obj else "",
            "ru_systeme_it": ru_systeme_it or "",
            "ru_systeme_nom": c.ru_it.nom_complete if c.ru_it else "",
            "ru_change_non_declare": ru_change_non_declare,
            "departement": c.departement.abreviation if c.departement else "",
        })

    return JsonResponse({
        "results": results,
        "total_r": len(ids_total_r),
        "page": page_obj.number,
        "num_pages": paginator.num_pages,
        "has_previous": page_obj.has_previous(),
        "has_next": page_obj.has_next(),
        "previous_page": page_obj.previous_page_number() if page_obj.has_previous() else None,
        "next_page": page_obj.next_page_number() if page_obj.has_next() else None,
    })

def get_n1_reels(departement):
    collaborateurs = Collaborateur.objects.filter(
        departement_id=departement.abreviation,
        lot__in=LOTS_OPERATEUR_N1,
    ).exclude(
        ru_it_id__isnull=True
    ).exclude(
        ru_it_id=F('it')
    )

    ru_ids = set(collaborateurs.values_list('ru_it_id', flat=True))

    ru_ids.discard(None)
    return ru_ids



@transaction.atomic
def synchroniser_maquettes_n1(departement, modifie_par_it=None):
    from declaration_effectif.views import calculer_niveaux_hierarchie
    _, l1, l2, l3, l4 = calculer_niveaux_hierarchie()

    tous_niveaux = l1 | l2 | l3 | l4
    its_departement = set(
        Collaborateur.objects
        .filter(departement=departement)
        .values_list("it", flat=True)
    )
    tous_niveaux = tous_niveaux & its_departement
    maquettes_existantes = {
        m.n1_id: m for m in MaquetteN1.objects.filter(n1_id__in=tous_niveaux)
    }

    # --- 1. Nouveaux managers (N+1 à N+4) ---
    nouveaux_ids = tous_niveaux - set(maquettes_existantes.keys())
    if nouveaux_ids:
        collabs = {c.it: c for c in Collaborateur.objects.filter(it__in=nouveaux_ids)}
        historiques = []
        for it_n1 in nouveaux_ids:
            obj = MaquetteN1.objects.create(
                n1_id=it_n1,
                departement=departement,
                A=0, T=0, P=0, C=0,
                actif=True,
                modifie_par_id=modifie_par_it,
            )
            n1_collab = collabs.get(it_n1)
            historiques.append(HistoriqueMaquetteN1(
                n1_id=it_n1,
                n1_it=it_n1,
                n1_nom=getattr(n1_collab, "nom_complete", ""),
                departement=departement,
                nature="AUTO_CREATION",
                ancien_A=None, ancien_T=None, ancien_P=None, ancien_C=None,
                nouveau_A=0, nouveau_T=0, nouveau_P=0, nouveau_C=0,
                modifie_par_id=modifie_par_it,
            ))
        HistoriqueMaquetteN1.objects.bulk_create(historiques)

    # --- 2. Managers déjà connus ---
    reactivations = []
    for it_n1 in tous_niveaux & set(maquettes_existantes.keys()):
        m = maquettes_existantes[it_n1]
        besoin_maj = False

        if not m.actif:
            m.actif = True
            besoin_maj = True
            reactivations.append(m)

        if m.departement_id != departement.abreviation:
            m.departement = departement
            besoin_maj = True

        if besoin_maj:
            m.save(update_fields=["actif", "departement", "date_maj"])

    if reactivations:
        historiques = [
            HistoriqueMaquetteN1(
                n1_id=m.n1_id, n1_it=m.n1_id, n1_nom=m.n1.nom_complete,
                departement=departement, nature="REACTIVATION",
                ancien_A=m.A, ancien_T=m.T, ancien_P=m.P, ancien_C=m.C,
                nouveau_A=m.A, nouveau_T=m.T, nouveau_P=m.P, nouveau_C=m.C,
                modifie_par_id=modifie_par_it,
            )
            for m in reactivations
        ]
        HistoriqueMaquetteN1.objects.bulk_create(historiques)

    # --- 3. Désactivation ---
    a_desactiver = MaquetteN1.objects.filter(
        departement=departement, actif=True
    ).exclude(n1_id__in=tous_niveaux)

    historiques = []
    for m in a_desactiver:
        historiques.append(HistoriqueMaquetteN1(
            n1_id=m.n1_id, n1_it=m.n1_id, n1_nom=m.n1.nom_complete if m.n1 else m.n1_id,
            departement=departement, nature="INACTIVATION",
            ancien_A=m.A, ancien_T=m.T, ancien_P=m.P, ancien_C=m.C,
            nouveau_A=m.A, nouveau_T=m.T, nouveau_P=m.P, nouveau_C=m.C,
            modifie_par_id=modifie_par_it,
        ))
    if historiques:
        a_desactiver.update(actif=False)
        HistoriqueMaquetteN1.objects.bulk_create(historiques)

    return tous_niveaux


@role_required('PILOT')
@require_POST
def synchroniser_maquettes_n1_view(request):
    it = request.session.get("it")
    departement = get_object_or_404(Departement, PILOT_id=it)
    synchroniser_maquettes_n1(departement, modifie_par_it=it)
    messages.success(request, "Maquettes synchronisées")
    return redirect("maquette")


@role_required('PILOT')
def maquette_n1_view(request):
    from declaration_effectif.views import calculer_niveaux_hierarchie

    pilot_it = request.session.get("it")
    if not pilot_it:
        return redirect("login")

    departement = get_object_or_404(Departement, PILOT_id=pilot_it)

    filter_it = request.GET.get('filter_it', '').strip()
    filter_nom = request.GET.get('filter_nom', '').strip()

    _, l1, l2, l3, l4 = calculer_niveaux_hierarchie()
    niveaux_autorises = l1 | l2 | l3 | l4

    queryset = MaquetteN1.objects.filter(
        actif=True, departement=departement, n1_id__in=niveaux_autorises
    ).select_related('n1', 'departement')

    if filter_it:
        queryset = queryset.filter(n1__it__icontains=filter_it)

    if filter_nom:
        queryset = queryset.filter(n1__nom_complete__icontains=filter_nom)

    liste_items = []
    for mq in queryset:
        liste_items.append({
            'n1': mq.n1,
            'maquette': mq
        })

    paginator = Paginator(liste_items, 10)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)

    context = {
        'page_obj': page_obj,
        'filter_it': filter_it,
        'filter_nom': filter_nom,
        'departement': departement,
    }
    return render(request, 'declaration_effectif/PILOT/maquette_n1.html', context)
@role_required('PILOT')
@require_POST
@transaction.atomic
def maquette_n1_update(request):
    pilot_it = request.session.get("it")
    if not pilot_it:
        return HttpResponseForbidden("Session expirée")

    departement = get_object_or_404(Departement, PILOT_id=pilot_it)

    n1_it = request.POST.get("n1_it")
    if not n1_it:
        return HttpResponseBadRequest("Paramètre n1_it manquant")

    def _safe_int(val):
        try:
            v = int(val)
            return v if v >= 0 else None
        except (TypeError, ValueError):
            return None

    A = _safe_int(request.POST.get("A"))
    T = _safe_int(request.POST.get("T"))
    P = _safe_int(request.POST.get("P"))
    C = _safe_int(request.POST.get("C"))

    if None in (A, T, P, C):
        return HttpResponseBadRequest("Les champs A, T, P, C doivent être des entiers >= 0")

    try:
        maquette = MaquetteN1.objects.select_for_update().get(n1_id=n1_it, departement=departement)
    except MaquetteN1.DoesNotExist:
        return HttpResponseBadRequest("Maquette N+1 introuvable pour ce département")

    ancien_A, ancien_T, ancien_P, ancien_C = maquette.A, maquette.T, maquette.P, maquette.C

    if (ancien_A, ancien_T, ancien_P, ancien_C) == (A, T, P, C):
        if request.headers.get('x-requested-with') == 'XMLHttpRequest':
            return JsonResponse({"success": True, "message": "Aucun changement"})
        messages.info(request, "Aucun changement détecté")
        return redirect("maquette_n1_view")

    maquette.A = A
    maquette.T = T
    maquette.P = P
    maquette.C = C
    maquette.save(update_fields=["A", "T", "P", "C", "date_maj"])

    HistoriqueMaquetteN1.objects.create(
        n1_id=maquette.n1_id,
        n1_it=maquette.n1_id,
        n1_nom=getattr(maquette.n1, "nom_complete", maquette.n1_id),
        departement=departement,
        nature="MODIFICATION",
        ancien_A=ancien_A, ancien_T=ancien_T, ancien_P=ancien_P, ancien_C=ancien_C,
        nouveau_A=A, nouveau_T=T, nouveau_P=P, nouveau_C=C,
        modifie_par_id=pilot_it,
    )

    if request.headers.get('x-requested-with') == 'XMLHttpRequest':
        return JsonResponse({"success": True, "message": "Maquette mise à jour", "total": maquette.total})
    messages.success(request, "Maquette mise à jour")
    return redirect("maquette")

def _dates_fin_de_mois(nb_annees=3):
    today = timezone.now().date()
    annee_courante = today.year
    annees = list(range(annee_courante - nb_annees + 1, annee_courante + 1))

    mois_cles, mois_labels, dates_ref = [], [], []
    for annee in annees:
        mois_max = today.month if annee == annee_courante else 12
        for m in range(1, mois_max + 1):
            mois_cles.append((annee, m))
            mois_labels.append(f"{m:02d}/{annee}")
            if annee == annee_courante and m == today.month:
                dates_ref.append(today)
            else:
                dates_ref.append(date(annee, m, monthrange(annee, m)[1]))
    return annees, mois_cles, mois_labels, dates_ref


def _regrouper_par_annee(mois_cles, valeurs):
    labels_par_annee, data_par_annee = {}, {}
    for (annee, mois), v in zip(mois_cles, valeurs):
        labels_par_annee.setdefault(annee, []).append(f"{mois:02d}")
        data_par_annee.setdefault(annee, []).append(v)
    return labels_par_annee, data_par_annee


class _MaquetteSnapshot:
    __slots__ = ("A", "T", "P", "C")
    def __init__(self, A=0, T=0, P=0, C=0):
        self.A, self.T, self.P, self.C = A, T, P, C


def get_maquettes_a_date(ru_ids, at_date):

    ru_ids = list(ru_ids)
    if not ru_ids:
        return {}

    entrees = (
        HistoriqueMaquetteN1.objects
        .filter(n1_id__in=ru_ids, date__date__lte=at_date)
        .order_by("n1_id", "-date", "-id")
    )
    resultat = {}
    vus = set()
    for h in entrees:
        if h.n1_id in vus:
            continue
        vus.add(h.n1_id)
        resultat[h.n1_id] = _MaquetteSnapshot(
            A=h.nouveau_A or 0, T=h.nouveau_T or 0,
            P=h.nouveau_P or 0, C=h.nouveau_C or 0,
        )
    return resultat

def get_maquettes_a_date_multi(ru_ids, dates):

    ru_ids = list(ru_ids)
    dates = sorted(set(dates))
    if not ru_ids or not dates:
        return {d: {} for d in dates}

    # ⚡ UNE SEULE requête pour tout l'historique jusqu'à la date max
    entrees = (
        HistoriqueMaquetteN1.objects
        .filter(n1_id__in=ru_ids, date__date__lte=dates[-1])
        .order_by("n1_id", "date", "id")
        .values_list("n1_id", "date", "nouveau_A", "nouveau_T",
                    "nouveau_P", "nouveau_C")
    )

    histo_par_n1 = defaultdict(list)
    for n1_id, dt, A, T, P, C in entrees:
        dt_date = dt.date() if hasattr(dt, "date") and callable(dt.date) else dt
        histo_par_n1[n1_id].append((dt_date, A or 0, T or 0, P or 0, C or 0))

    resultat = {}
    for d in dates:
        d_date = d.date() if hasattr(d, "date") and callable(d.date) else d

        snap_map = {}
        for n1_id, entries in histo_par_n1.items():
            dernier = None
            for e in entries:
                if e[0] <= d_date:
                    dernier = e
                else:
                    break  # liste triée par date croissante
            if dernier:
                snap_map[n1_id] = _MaquetteSnapshot(
                    A=dernier[1], T=dernier[2], P=dernier[3], C=dernier[4],
                )
        resultat[d] = snap_map
    return resultat

def get_managers_racines(ids_managers):
    ids_managers = set(ids_managers)
    if not ids_managers:
        return set()

    paires = Collaborateur.objects.filter(it__in=ids_managers).values_list("it", "ru_it_id")
    return {
        it_m for it_m, ru_it_id in paires
        if not ru_it_id or ru_it_id not in ids_managers
    }


def calculer_maquette_totale_perimetre(ids_managers, departement=None):
    racines = get_managers_racines(ids_managers)
    maquette_map = get_maquettes_n1_map(racines, departement=departement)
    total = sum(m.total for m in maquette_map.values())
    somme_ap = sum((m.A or 0) + (m.P or 0) for m in maquette_map.values())
    somme_ce = sum((m.C or 0) + (m.T or 0) for m in maquette_map.values())
    return total, maquette_map, racines ,somme_ap ,somme_ce


def get_ru_reel_et_departs_a_dates(dates):
    dates = sorted(set(dates))
    if not dates:
        return {}

    rows = list(
        declaration_effectif.objects
        .filter(date__lte=dates[-1])
        .order_by("collaborateur_it_id", "date", "id")
        .values_list("collaborateur_it_id", "date", "nature", "Ru_id", "nv_Ru_id")
    )
    par_collab = defaultdict(list)
    for cid, dt, nat, ru_id, nv_ru_id in rows:
        par_collab[cid].append((dt, nat, ru_id, nv_ru_id))

    resultat = {}
    for d in dates:
        ru_reel = {}
        departs = set()
        for cid, decls in par_collab.items():
            dts = [x[0] for x in decls]
            pos = bisect.bisect_right(dts, d) - 1
            if pos < 0:
                continue
            _, nat, ru_id, nv_ru_id = decls[pos]
            if nat == "D":
                departs.add(cid)
            elif nat == "C" and nv_ru_id:
                ru_reel[cid] = nv_ru_id
            elif ru_id:
                ru_reel[cid] = ru_id
        resultat[d] = (ru_reel, departs)
    return resultat


def get_tous_les_it_sous_reel_evolution(it, dates):
    dates = sorted(set(dates))
    if not dates:
        return {}

    par_date = get_ru_reel_et_departs_a_dates(dates)
    tous_collabs = list(Collaborateur.objects.values_list("it", "ru_it_id"))

    resultat = {}
    for d in dates:
        ru_reel, departs = par_date[d]

        enfants_par_ru = {}
        for c_it, c_ru_it in tous_collabs:
            if c_it in departs:
                continue
            ru_effectif = ru_reel.get(c_it, c_ru_it)
            if ru_effectif:
                enfants_par_ru.setdefault(ru_effectif, []).append(c_it)

        def _recurse(it_courant, vus):
            niveau = set(enfants_par_ru.get(it_courant, [])) - vus
            if not niveau:
                return set()
            vus |= niveau
            tous = set(niveau)
            for sous_it in niveau:
                tous |= _recurse(sous_it, vus)
            return tous

        resultat[d] = _recurse(it, set())

    return resultat


CACHE_TTL_SHORT = 60
CACHE_TTL_MEDIUM = 300
CACHE_TTL_LONG = 900


# ============================================================
# 1. CACHE HIÉRARCHIE
# ============================================================

def get_hierarchie_complete():

    cache_key = "hierarchie_complete_v1"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    from Collaborateur.views import (
        get_managers_its,
        get_managers_avec_operateurs_aop,
        get_hierarchie_map,
        niveau_hierarchique,
    )

    managers_its = get_managers_its()
    managers_aop = get_managers_avec_operateurs_aop()
    mapping = get_hierarchie_map()

    cache_niveau = {}
    for it in managers_its:
        niveau_hierarchique(it, managers_its, mapping, cache_niveau,
                            managers_aop=managers_aop)

    niveau_par_it = {it: min(n, 4) for it, n in cache_niveau.items() if n and n >= 1}

    l1 = {it for it, n in niveau_par_it.items() if n == 1}
    l2 = {it for it, n in niveau_par_it.items() if n == 2}
    l3 = {it for it, n in niveau_par_it.items() if n == 3}
    l4 = {it for it, n in niveau_par_it.items() if n == 4}

    result = {
        "managers_its": managers_its,
        "managers_aop": managers_aop,
        "mapping": mapping,
        "niveau_par_it": niveau_par_it,
        "l1": l1, "l2": l2, "l3": l3, "l4": l4,
        "tous_managers_classes": l1 | l2 | l3 | l4,
    }
    cache.set(cache_key, result, CACHE_TTL_MEDIUM)
    return result


def get_vrais_n1_purs():
    cache_key = "vrais_n1_purs_v1"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    h = get_hierarchie_complete()
    vrais = set()
    for it in (h["l1"] & h["managers_aop"]):
        if it in (h["l2"] | h["l3"] | h["l4"]):
            continue
        vrais.add(it)

    cache.set(cache_key, vrais, CACHE_TTL_MEDIUM)
    return vrais


def invalider_tout_cache():
    cache.delete_many([
        "hierarchie_complete_v1",
        "vrais_n1_purs_v1",
        "managers_its",
        "managers_avec_aop",
        "hierarchie_map",
        "niveaux_hierarchie_v1",
        "ru_reel_et_departs",
    ])
    try:
        cache.delete_pattern("reel_batch_*")
        cache.delete_pattern("tous_n1_*")
        cache.delete_pattern("maquettes_multi_*")
    except AttributeError:
        # delete_pattern n'existe que sur Redis/django-redis
        cache.clear()


# ============================================================
# 2. CACHE EFFECTIFS PAR RU
# ============================================================

def get_effectif_batch(ru_ids):
    ru_ids = tuple(sorted(set(ru_ids)))
    if not ru_ids:
        return {}

    cache_key = f"reel_batch_{hash(ru_ids)}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    from declaration_effectif.views import _reel_effectif_batch
    resultat = _reel_effectif_batch(list(ru_ids))

    cache.set(cache_key, resultat, CACHE_TTL_SHORT)
    return resultat


# ============================================================
# 3. CACHE MAQUETTES MULTI-DATES
# ============================================================

def get_maquettes_multi(ru_ids, dates):
    ru_ids = tuple(sorted(set(ru_ids)))
    dates = tuple(sorted(set(dates)))
    if not ru_ids or not dates:
        return {d: {} for d in dates}

    cache_key = f"maquettes_multi_{hash(ru_ids)}_{hash(dates)}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    from Collaborateur.views import get_maquettes_a_date_multi
    resultat = get_maquettes_a_date_multi(list(ru_ids), list(dates))

    cache.set(cache_key, resultat, CACHE_TTL_MEDIUM)
    return resultat


# ============================================================
# 4. CACHE "TOUS LES N+1 SOUS UN IT"
# ============================================================

def get_tous_les_n1_cached(it):
    if not it:
        return set()

    cache_key = f"tous_n1_{it}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    from Collaborateur.views import get_tous_les_n1
    resultat = get_tous_les_n1(it)

    cache.set(cache_key, resultat, CACHE_TTL_MEDIUM)
    return resultat


# ============================================================
# 5. CACHE "SOUS-MANAGERS GROUPÉS"
# ============================================================

def get_sous_managers_groupes(parent_ids, tous_managers_classes):
    parent_ids = tuple(sorted(set(parent_ids)))
    if not parent_ids:
        return {}

    cache_key = f"sous_managers_{hash(parent_ids)}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    tous = Collaborateur.objects.filter(
        ru_it_id__in=parent_ids,
        it__in=tous_managers_classes,
    ).values_list("ru_it_id", "it")

    resultat = defaultdict(list)
    for parent_id, child_it in tous:
        resultat[parent_id].append(child_it)

    resultat = dict(resultat)
    cache.set(cache_key, resultat, CACHE_TTL_SHORT)
    return resultat


def get_sous_operateurs_groupes(parent_ids, tous_managers_classes):
    parent_ids = tuple(sorted(set(parent_ids)))
    if not parent_ids:
        return {}

    cache_key = f"sous_operateurs_{hash(parent_ids)}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    tous = Collaborateur.objects.filter(
        ru_it_id__in=parent_ids,
    ).exclude(
        it__in=tous_managers_classes
    ).values_list("ru_it_id", "it")

    resultat = defaultdict(list)
    for parent_id, child_it in tous:
        resultat[parent_id].append(child_it)

    resultat = dict(resultat)
    cache.set(cache_key, resultat, CACHE_TTL_SHORT)
    return resultat

def reelEff_batch_multi(ru_ids, managers_its=None):
    if managers_its is None:
        managers_its = get_managers_its()

    ru_ids = list(ru_ids)
    ru_ids_set = set(ru_ids)
    if not ru_ids:
        return {}

    # 1) Membres rattachés
    membres_par_ru = defaultdict(set)
    for c_it, c_ru_it in (
        Collaborateur.objects
        .filter(ru_it_id__in=ru_ids)
        .exclude(it=F('ru_it_id'))
        .values_list('it', 'ru_it_id')
    ):
        membres_par_ru[c_ru_it].add(c_it)

    tous_membres = set()
    for s in membres_par_ru.values():
        tous_membres |= s

    # 2) Dernière déclaration par membre (pour A autre RU)
    derniere_decl_par_collab = {}
    if tous_membres:
        for cid, nat, ru_id_decl, dt in (
            declaration_effectif.objects
            .filter(collaborateur_it_id__in=tous_membres)
            .order_by("collaborateur_it_id", "-date", "-id")
            .values_list("collaborateur_it_id", "nature", "Ru_id", "date")
        ):
            if cid not in derniere_decl_par_collab:
                derniere_decl_par_collab[cid] = (nat, ru_id_decl, dt)

    # 3) C/D sortants par RU
    sortants_par_ru = defaultdict(set)
    for ru_id, cid in (
        declaration_effectif.objects
        .filter(Ru_id__in=ru_ids, nature__in=["C", "D"])
        .values_list("Ru_id", "collaborateur_it_id")
    ):
        sortants_par_ru[ru_id].add(cid)

    # 4) Dernière date par RU
    dernieres_dates_par_ru = dict(
        declaration_effectif.objects
        .filter(Ru_id__in=ru_ids)
        .values('Ru_id').annotate(d=Max('date'))
        .values_list('Ru_id', 'd')
    )

    # 5) A/V ajoutés à la dernière déclaration du RU
    ajouts_par_ru = defaultdict(set)
    for ru_id, cid, dt in (
        declaration_effectif.objects
        .filter(Ru_id__in=ru_ids, nature__in=["A", "V"])
        .values_list("Ru_id", "collaborateur_it_id", "date")
    ):
        if dernieres_dates_par_ru.get(ru_id) == dt:
            ajouts_par_ru[ru_id].add(cid)

    # 6) C entrants
    entrants_candidats = set(
        declaration_effectif.objects
        .filter(nature="C", nv_Ru_id__in=ru_ids)
        .values_list("collaborateur_it_id", flat=True)
    )

    entrants_valides_par_ru = defaultdict(set)
    if entrants_candidats:
        toutes_decls_candidats = (
            declaration_effectif.objects
            .filter(collaborateur_it_id__in=entrants_candidats)
            .order_by("collaborateur_it_id", "-date", "-id")
            .values_list("collaborateur_it_id", "nature", "nv_Ru_id")
        )
        derniere_par_collab = {}
        for cid, nat, nv_ru_id in toutes_decls_candidats:
            derniere_par_collab.setdefault(cid, (nat, nv_ru_id))

        for cid, (nat, nv_ru_id) in derniere_par_collab.items():
            if nat == "C" and nv_ru_id in ru_ids_set:
                entrants_valides_par_ru[nv_ru_id].add(cid)

    # 7) Fusion
    resultat = {}
    for ru_id in ru_ids:
        base = membres_par_ru.get(ru_id, set())

        # C/D sortants
        sortis = sortants_par_ru.get(ru_id, set())

        # A faits par un autre RU
        exclus_a_autre_ru = set()
        for cid in base:
            nat, ru_decl, _ = derniere_decl_par_collab.get(cid, (None, None, None))
            if nat == "A" and ru_decl != ru_id:
                exclus_a_autre_ru.add(cid)

        base = base - sortis - exclus_a_autre_ru

        ajouts = ajouts_par_ru.get(ru_id, set())
        entrants = entrants_valides_par_ru.get(ru_id, set())

        operateurs = base | ajouts | entrants
        operateurs.discard(ru_id)

        resultat[ru_id] = operateurs

    return resultat