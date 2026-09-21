from datetime import datetime

from django.db import migrations


def preencher_ano(apps, schema_editor):
    Turma = apps.get_model('aluno', 'Turma')
    Turma.objects.filter(ano__isnull=True).update(ano=datetime.now().year)


class Migration(migrations.Migration):

    dependencies = [
        ('aluno', '0016_remove_nota_ano_turma_ano'),
    ]

    operations = [
        migrations.RunPython(preencher_ano, migrations.RunPython.noop),
    ]
