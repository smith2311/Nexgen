from django.core.management.base import BaseCommand, CommandError
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.db import transaction

User = get_user_model()
ROOT_USER_GROUP = 'Root User'


class Command(BaseCommand):
    help = 'Assign the Root User role to a user by email (clears any existing holder first)'

    def add_arguments(self, parser):
        parser.add_argument('email', type=str, help='Email address of the user to make Root User')

    def handle(self, *args, **options):
        email = options['email'].strip().lower()
        try:
            user = User.objects.get(email__iexact=email)
        except User.DoesNotExist:
            raise CommandError(f'No user found with email: {email}')

        root_group, _ = Group.objects.get_or_create(name=ROOT_USER_GROUP)

        with transaction.atomic():
            root_group.user_set.clear()
            user.groups.add(root_group)

        label = user.get_full_name() or user.username
        self.stdout.write(self.style.SUCCESS(
            f'Root User assigned to: {label} ({user.email})'
        ))
