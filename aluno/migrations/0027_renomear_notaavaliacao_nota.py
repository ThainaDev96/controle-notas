from django.db import migrations, models


def renomear_objetos_resumo(apps, schema_editor):
    # O RenameField renomeia só a coluna; índices e restrições continuam com
    # "resumo" no nome. Aqui eles acompanham a nova coluna (nota_id).
    if schema_editor.connection.vendor != 'postgresql':
        return
    tabela = 'aluno_notaavaliacao'
    with schema_editor.connection.cursor() as cursor:
        cursor.execute(
            "SELECT conname FROM pg_constraint WHERE conrelid = %s::regclass AND conname LIKE %s",
            [tabela, '%resumo%'],
        )
        for (nome,) in cursor.fetchall():
            cursor.execute(
                f'ALTER TABLE {tabela} RENAME CONSTRAINT "{nome}" TO "{nome.replace("resumo", "nota")}"'
            )
        cursor.execute(
            "SELECT indexname FROM pg_indexes WHERE tablename = %s AND indexname LIKE %s",
            [tabela, '%resumo%'],
        )
        for (nome,) in cursor.fetchall():
            cursor.execute(f'ALTER INDEX "{nome}" RENAME TO "{nome.replace("resumo", "nota")}"')


class Migration(migrations.Migration):

    dependencies = [
        ('aluno', '0026_notaavaliacao_resumo_final'),
    ]

    operations = [
        # 1) libera o nome "nota" no Python; 2) o FK vira "nota" (coluna nota_id);
        # 3) o valor volta a se chamar "nota" no banco, como no dicionário de dados.
        migrations.RenameField(model_name='notaavaliacao', old_name='nota', new_name='nota_obtida'),
        migrations.RenameField(model_name='notaavaliacao', old_name='resumo', new_name='nota'),
        migrations.AlterField(
            model_name='notaavaliacao',
            name='nota_obtida',
            field=models.FloatField(blank=True, db_column='nota', null=True, verbose_name='Nota obtida'),
        ),
        migrations.RunPython(renomear_objetos_resumo, migrations.RunPython.noop),
    ]
