from django.core.management.base import BaseCommand
from django.contrib.auth.models import User, Group
from aluno.models import Disciplina, Nota, Turma, Matricula



class Command(BaseCommand):
    help = "Popula o banco com dados de teste"
    #chama métodos
    def handle(self, *args, **kwargs):
        self.migrar_notas()

    def migrar_notas(self):
        lista_notas = list(Nota.objects.all())

        for nota in lista_notas:


