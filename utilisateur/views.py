from django.shortcuts import render, redirect
from utilisateur.models import utilisateur
from Collaborateur.models import  Departement , Collaborateur ,Unite
from django.contrib.auth.hashers import make_password , check_password
from django.db.models import Q ,Count
from django.contrib import messages
from django.utils import timezone
from declaration_effectif.models import declaration_effectif ,Alert ,historique
from django.http import JsonResponse
from datetime import date
from declaration_effectif.views import difference , histo_aff
from Collaborateur.views import rec ,Ru_Rg,liste_N1_pr_N3,Rg_Dur ,reelEff
from django.db.models import Sum
from .decorators import role_required
from datetime import timedelta
import json

# rederiger vers la page login et connercter utilisateur
def login_view(request):
    if request.method == "POST":
        it = request.POST.get("it")
        password = request.POST.get("password")

        try:
            utilis = utilisateur.objects.get(pk=it)
            collab = utilis.it

            if check_password(password, utilis.password):
                request.session["it"] = utilis.it.it
                request.session["role"] = utilis.role
                request.session["nom"] = collab.nom_complete
                roles_disponibles = []
    
                if utilis.N1 == 1:
                    roles_disponibles.append("N+1")
                if utilis.N2 == 1:
                            roles_disponibles.append("N+2")
                if utilis.N3 == 1:
                            roles_disponibles.append("N+3")
                if utilis.N4 == 1:
                            roles_disponibles.append("N+4")
                    
                        # Stockage en session
                request.session['roles_disponibles'] = roles_disponibles
                if utilis.role == "N+1":
                    return redirect('ru')

                elif utilis.role == "N+2":
                    return redirect("dashboard_rg")
                elif utilis.role == "N+3":
                    return redirect("DUR")
                    
                elif utilis.role == "N+4":
                    return redirect('page_N4')
            else:
                return render(
                    request,
                    "utilisateur/login.html",
                    {"message": "Mot de passe incorrect"}
                )
        except utilisateur.DoesNotExist:
            return render(
                request,
                "utilisateur/login.html",
                {"message": "Identifiant introuvable"}
            )
    else:
        return render(
                request,
                "utilisateur/login.html")
    
# Importe tes modèles ici si besoin :
# from .models import utilisateur, Collaborateur

def register_view(request):
    if request.method == "POST":
        it = request.POST.get('it')
        password = request.POST.get('password')

        # 1. Validation des champs remplis
        if not it or not password:
            return render(request, "utilisateur/register.html", {
                'message': "vous devez remplir tt les champs"
            })
            
        # 2. Vérification si l'utilisateur existe déjà (avec return immédiat)
        if utilisateur.objects.filter(it_id=it).exists():
            return render(request, "utilisateur/register.html", {
                'message': "Cet utilisateur est déjà enregistré"
            })

        # =========================================================
        # TA LOGIQUE DE FILTRAGE EXACTE (IDENTIQUE À TON CODE)
        # =========================================================
        managers_ids = set(
            Collaborateur.objects.exclude(ru_it_id__isnull=True)
            .values_list("ru_it_id", flat=True)
        )

        operateurs = Collaborateur.objects.exclude(it__in=managers_ids)

        # Niveau N1
        liste_N1 = set(operateurs.values_list("ru_it_id", flat=True))
        liste1 = Collaborateur.objects.filter(it__in=liste_N1)
        l1 = set(liste1.values_list("it", flat=True))

        # Niveau N2
        liste_N2 = set(liste1.values_list("ru_it_id", flat=True))
        liste_N2.discard(None)
        liste2 = Collaborateur.objects.filter(it__in=liste_N2)
        l2 = set(liste2.values_list("it", flat=True))

        # Niveau N3
        liste_N3 = set(liste2.values_list("ru_it_id", flat=True))
        liste_N3.discard(None)
        liste3 = Collaborateur.objects.filter(it__in=liste_N3)
        l3 = set(liste3.values_list("it", flat=True))

        # Niveau N4
        liste_N4 = set(liste3.values_list("ru_it_id", flat=True))
        liste_N4.discard(None)
        liste4 = Collaborateur.objects.filter(it__in=liste_N4)
        l4 = set(liste4.values_list("it", flat=True))

        # =========================================================
        # GESTION DES DRAPEAUX 1 ET 0 (CORRECTION DU BUG)
        # =========================================================
        n1_flag = 1 if it in l1 else 0
        n2_flag = 1 if it in l2 else 0
        n3_flag = 1 if it in l3 else 0
        n4_flag = 1 if it in l4 else 0

        # Détermination du rôle principal
        role = None
        if it in l4:
            role = "N+4"
        elif it in l3 :
            role = "N+3"
        elif it in l2:
            role = "N+2"
        elif it in l1:
            role = "N+1"

        # Si l'utilisateur n'est dans aucune liste (0 partout)
        if not role:
            return render(request, "utilisateur/accesInterdi.html")

        # Enregistrement en base avec les valeurs 1 ou 0
        utilisateur.objects.create(
            it_id=it,
            password=make_password(password),
            role=role,
            N1=n1_flag,
            N2=n2_flag,
            N3=n3_flag,
            N4=n4_flag
        )

        return render(request, "utilisateur/login.html")

    else:
        return render(request, "utilisateur/register.html")

#deriger vers la table de bord de Ru
@role_required('N+1')
def tableau(request):
    # 1. Récupération des informations de session & RU
    it = request.session.get("it")
    ru = Collaborateur.objects.get(it=it)
    role = request.session.get("role")

    # 2. Operateurs Système & Réel
    operateurs_systeme = Collaborateur.objects.filter(ru_it_id=it)
    syste = operateurs_systeme.count()

    operateurs_reel = reelEff(request)  # fonction personnalisée ds views Collaborateur
    reel = operateurs_reel.count()

    # Différences (Système vs Réel)
    diff = difference(request)  # fonction personnalisée ds views declaration effectif
    systeme = diff["systeme1"]
    reel1 = diff["reel1"]

    # 3. Récupération de la Maquette & Unité
    unite = None
    maquette = 0
    A = O = E = P = C = 0

    if ru.unite_id:
        try:
            unite = Unite.objects.get(abreviation=ru.unite_id)
            maquette = unite.maquette or 0
            A = unite.A or 0
            O = unite.O or 0
            E = unite.T or 0
            P = unite.P or 0
            C = unite.C or 0
        except Unite.DoesNotExist:
            pass

    # 4. Calculs par Lots / Postes
    Opr = operateurs_reel.filter(post="O").count()
    Tr = operateurs_reel.filter(post="T").count()
    Pr = operateurs_reel.filter(lot="P").count()
    Ar = operateurs_reel.filter(lot="A").count()
    OLr = operateurs_reel.filter(lot="O").count()
    Er = operateurs_reel.filter(lot="E").count()
    Cr = operateurs_reel.filter(lot="C").count()

    # Écarts vs Maquette
    diff_r_m = reel - maquette
    diff_s_m = syste - maquette

    # 6. Date de dernière déclaration
    der = declaration_effectif.objects.filter(Ru_id=it).order_by("-date").first()
    date_declaration = der.date if der else timezone.localdate()

    # ------------------------------------------------------------------
    # 7. Données pour le Graphique (7 derniers jours)
    # ------------------------------------------------------------------
    today = timezone.now().date()
    dates = [today - timedelta(days=i) for i in range(6, -1, -1)]

    labels_list = [d.strftime('%d %b') for d in dates]
    data_list = []

    for d in dates:
        derniere_declaration = declaration_effectif.objects.filter(
            Ru_id=it,
            date__lte=d
        ).order_by("-date").first()

        if derniere_declaration:
            count = declaration_effectif.objects.filter(
                Ru_id=it,
                date=derniere_declaration.date,
                nature__in=["A", "V"]
            ).count()
        else:
            count = syste  # valeur système par défaut si aucune déclaration n'existe encore

        data_list.append(count)

    # ------------------------------------------------------------------
    # 8. Rendu Final (Dictionnaire de contexte unique et à plat)
    # ------------------------------------------------------------------
    context = {
        # Graphique Chart.js (Encodés en JSON)
        'chart_labels': json.dumps(labels_list),
        'chart_data': json.dumps(data_list),
        'selected_ru_nom': getattr(ru, 'nom_complete', f"RU #{it}"),

        # Informations Générales
        'operateurs_systeme': operateurs_systeme,
        'syste': syste,
        'reel': reel,
        'diff_r_m': diff_r_m,
        'diff_s_m': diff_s_m,
        'date': date_declaration,

        # Tableaux de détails (Différences)
        'reel1': reel1,
        'systeme1': systeme,

        # Lots Réel vs Maquette
        'Opr': Opr, 'Tr': Tr,
        'Pr': Pr, 'Ar': Ar, 'OLr': OLr, 'Er': Er, 'Cr': Cr,
        'A': A, 'C': C, 'P': P, 'E': E, 'O': O,
        'maquette': maquette,

        # Graphique "Réel vs Maquette par lot" (Encodés en JSON pour éviter
        # tout "None" littéral côté template qui casserait le JS)
        'reel_par_lot_json': json.dumps([Ar, OLr, Pr, Er, Cr]),
        'maquette_par_lot_json': json.dumps([A, O, P, E, C]),
    }
    return render(request, "declaration_effectif/Tableau_de_bord.html", context)
#verifier que Ru existe
def verifier(request):
    it=request.GET.get("q","")
    if it:
        nbr=utilisateur.objects.filter(it_id=it,role="N+1").exists()
        return JsonResponse({"valide": nbr})
    
def deconnecter(request):
    request.session.flush()
    return redirect("login")

#rederiger vers la page dashboard du Rg
@role_required("N+2")
def dashboard_rg(request):
    it = request.session.get("it")
    liste_ru = Ru_Rg(it)
    liste_ru_stats = []
    effectif_reel = 0
    effectif_syste = 0
    maquette_total = 0

    unite_abr = set(liste_ru.values_list("unite_id", flat=True))
    maint = timezone.localdate()

    declaration = declaration_effectif.objects.filter(
        date=maint, Ru_id__in=liste_ru.values_list("it", flat=True)
    )
    liste_declares = set(declaration.values_list("Ru_id", flat=True))

    liste_ru_avec_operateurs = set(
        Collaborateur.objects.filter(
            ru_it_id__in=liste_ru.values_list("it", flat=True)
        )
        .values_list("ru_it_id", flat=True)
        .distinct()
    )

    # RU n'ayant pas encore effectué leur déclaration
    non_valides = (
        Collaborateur.objects.filter(it__in=liste_ru_avec_operateurs)
        .exclude(it__in=liste_declares)
        .count()
    )

    for u in Unite.objects.filter(abreviation__in=unite_abr):
        maquette_total += u.maquette

    for a in liste_ru:
        # Effectif système
        systeme = Collaborateur.objects.filter(ru_it_id=a.it).count()
        effectif_syste += systeme

        # Dernière déclaration de ce RU
        derniere_declaration = (
            declaration_effectif.objects.filter(Ru_id=a.it)
            .order_by("-date")
            .first()
        )

        # Effectif réel
        if derniere_declaration:
            reel = declaration_effectif.objects.filter(
                Ru_id=a.it,
                date=derniere_declaration.date,
                nature__in=["A", "V"],
            ).count()
        else:
            reel = systeme

        unite_abrev = a.unite_id
        maquette = 0
        if unite_abrev:
            u = Unite.objects.filter(abreviation=unite_abrev).first()
            if u and u.maquette:
                maquette = u.maquette

        effectif_reel += reel

        liste_ru_stats.append({
            "matricule": a.matricule,
            "nom_complete": a.nom_complete,
            "lot": a.lot,
            "unite": a.unite_id,
            "dpt": a.departement_id,
            "reel": reel,
            "systeme": systeme,
            "maquette": maquette,
            "MS": (
                maquette - systeme
                if maquette is not None and systeme is not None
                else 0
            ),
            "MR": (
                maquette - reel if maquette is not None and reel is not None else 0
            ),
        })

    # Calcul des écarts globaux pour les badges du haut
    MR = maquette_total - effectif_reel
    MS = maquette_total - effectif_syste

    context = {
        "effectif_reel": effectif_reel,
        "effectif_syste": effectif_syste,
        "maquette_total": maquette_total,
        "non_valides": non_valides,  # Passer le nombre global au template
        "liste_ru_stats": liste_ru_stats,
        "MR": MR,
        "MS": MS,
        "maint": maint,
    }

    return render(request, "declaration_effectif/RG/Dashboardrg.html", context)

def alertes(request):
    it = request.session.get("it")
    if not it:
        return JsonResponse({"statu": "false", "error": "Non authentifié"}, status=401)
    
    # Récupère et met à jour en une seule requête SQL
    count_updated = Alert.objects.filter(recepteur=it, lu="false").update(lu="true")
    
    if count_updated > 0:
        return JsonResponse({"statu": "true", "count": count_updated})
    else:
        return JsonResponse({"statu": "false", "message": "Aucune alerte non lue"})


def changer_role(request, nouveau_role):
    roles_dispo = request.session.get('roles_disponibles', [])
    it=request.session.get("it")
    user=utilisateur.objects.get(it=it)
    if nouveau_role in roles_dispo:
        request.session['role'] = nouveau_role
        user.role=nouveau_role
        user.save()
        messages.success(request, f"Rôle actif changé vers {nouveau_role}")
        return redirect('login')
    else:
        messages.error(request, "Vous n'avez pas ce rôle.")
    return redirect(request.META.get('HTTP_REFERER', 'ru'))


def notifications(request):
    it = request.session.get("it")
    alert = Alert.objects.filter(recepteur=it)
    nv = alert.filter(lu="false").count()
    return {
        "notifications": alert,
        "nb_notifications": nv,
    }

def test(request):
    managers_ids = set(
                Collaborateur.objects.exclude(ru_it_id__isnull=True)
                .values_list("ru_it_id", flat=True)
            )
    
    operateurs = Collaborateur.objects.exclude(it__in=managers_ids)
    
            # Niveau N1
    liste_N1 = set(operateurs.values_list("ru_it_id", flat=True))
    liste1 = Collaborateur.objects.filter(it__in=liste_N1)
    l1 = set(liste1.values_list("it", flat=True))
    
            # Niveau N2
    liste_N2 = set(liste1.values_list("ru_it_id", flat=True))
    liste_N2.discard(None)
    liste2 = Collaborateur.objects.filter(it__in=liste_N2)
    l2 = set(liste2.values_list("it", flat=True))
    
            # Niveau N3
    liste_N3 = set(liste2.values_list("ru_it_id", flat=True))
    liste_N3.discard(None)
    liste3 = Collaborateur.objects.filter(it__in=liste_N3)
    l3 = set(liste3.values_list("it", flat=True))
    
            # Niveau N4
    liste_N4 = set(liste3.values_list("ru_it_id", flat=True))
    liste_N4.discard(None)
    liste4 = Collaborateur.objects.filter(it__in=liste_N4)
    l4 = set(liste4.values_list("it", flat=True))
    return render(request, "utilisateur/test.html", {"l1": l1, "l2": l2, "l3": l3, "l4": l4})