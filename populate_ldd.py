from accounts.models import Daara, LDD

# créer un LDD par défaut
ldd_default = LDD.objects.create(code="DEFAULT", name="LDD par défaut")

# assigner aux daaras existants
for daara in Daara.objects.all():
    daara.ldd = ldd_default
    daara.save()