"""Retira contraseñas publicadas sin tocar claves que ya fueron rotadas.

No se restauran secretos conocidos al revertir esta migración.
"""
from django.db import migrations
from django.contrib.auth.hashers import check_password
from django.utils import timezone


DEMO_HASHES = (
    'pbkdf2_sha256$600000$ELwd6GWDUmPQobJklIVC0c$K+o8b+nDTCjhXq3jUFWm7Yf+0KZSC/SwcX9UCsbX15k=',
    'pbkdf2_sha256$600000$p6BtgfsiXTUC$q93qU84TWURm0l59XsRPVRhV6x0XcDTlMnPGt861wnY=',
    'pbkdf2_sha256$600000$QpfkXs9oOpp2$XIbPiM6zQv7DQDZVJYGw9aLovhOduwwz/mIh9HgF+HA=',
    'pbkdf2_sha256$600000$JJKomP0E9S6u$XGFPsbnHOxQvkg9V3/QqK7NE0JBf++d455Znv+6bbRc=',
    'pbkdf2_sha256$600000$6wkG3dhQoos2$UkfVpMM0Fe0EzrOJOW8/jTL/HkudVJgJa3LF4MxLTUE=',
    'pbkdf2_sha256$600000$kdbmcYOAfWni$ej6FDIzlXAq2KYrFv4P8OpS5bQcrkb0p9NkYMRZIBEk=',
    'pbkdf2_sha256$1500000$Zrb4ZOEiEMlB58WPN5N8sA$EaQp2gpEC2kqG5/y4WSqIsHfPwgKk8DiJCLBbVoMlcA=',
    'pbkdf2_sha256$1500000$MdLUbAiuonT4HXi5AO4TJ5$vyBvnOYQCyvzlnoPY7EWkQe6puZDlL0z15GynulgBjM=',
)

DEMO_PASSWORDS = {
    'kurizd@djtry.local': '0330',
    'admin@amis.org': 'Amis2026!',
    'aspirante@amis.org': 'Amis2026!',
    **{f'kurizd@{rol}.com': '1234' for rol in (
        'administrador', 'reclutador', 'empresa', 'consulta', 'aspirante',
    )},
}


def desactivar_claves_demo(apps, schema_editor):
    Usuario = apps.get_model('core', 'Usuario')
    alias = schema_editor.connection.alias
    cuentas = Usuario.objects.using(alias)
    ids = set(cuentas.filter(password_hash__in=DEMO_HASHES).values_list('id', flat=True))
    # Un login puede haber actualizado el algoritmo o costo del hash sin
    # cambiar la contraseña pública. También retirar esos accesos.
    for email, password in DEMO_PASSWORDS.items():
        for cuenta in cuentas.filter(email__iexact=email):
            if check_password(password, cuenta.password_hash):
                ids.add(cuenta.pk)
    ahora = timezone.now()
    cuentas.filter(pk__in=ids).update(password_hash='!demo-disabled', actualizado_en=ahora)
    # La relación usuario de este modelo unmanaged no figura en el estado
    # histórico de Django, pero sí en el esquema SQL.
    with schema_editor.connection.cursor() as cursor:
        cursor.execute(
            'UPDATE sesiones SET revocada_en = %s '
            'WHERE usuario_id = ANY(%s::uuid[]) AND revocada_en IS NULL',
            [ahora, list(ids)],
        )


class Migration(migrations.Migration):
    dependencies = [('core', '0032_reacciones_comentarios')]
    operations = [migrations.RunPython(desactivar_claves_demo, migrations.RunPython.noop)]
