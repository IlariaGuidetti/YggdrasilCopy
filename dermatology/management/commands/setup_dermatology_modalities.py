from django.core.management.base import BaseCommand
from common.models import Project, Modality


class Command(BaseCommand):
    help = 'Create Dermatology project and register clinical photo modality'

    def handle(self, *args, **options):
        project, project_created = Project.objects.get_or_create(
            slug='dermatology',
            defaults={
                'name': 'Dermatology',
                'description': 'Clinical photography of skin lesions project',
                'icon': 'fas fa-user-md',
                'domain': 'dermatology',
                'is_active': True,
            }
        )

        if project_created:
            self.stdout.write(self.style.SUCCESS(f'Created project: {project.name}'))
        else:
            self.stdout.write(self.style.WARNING(f'Project already exists: {project.name}'))

        modalities_data = [
            {
                'name': 'Clinical Photo',
                'slug': 'clinical_photo',
                'domain': 'dermatology',
                'description': 'Clinical photograph of a skin lesion (.jpg, .jpeg, .png, .bmp)',
                'icon': 'fas fa-camera',
                'label': 'Clinical Photo',
                'supported_extensions': ['.jpg', '.jpeg', '.png', '.bmp'],
                'requires_multiple_files': False,
                'is_active': True,
            },
        ]

        for modality_data in modalities_data:
            modality, created = Modality.objects.get_or_create(
                slug=modality_data['slug'],
                defaults=modality_data,
            )

            if created:
                self.stdout.write(self.style.SUCCESS(f'Created modality: {modality.name}'))
            else:
                self.stdout.write(self.style.WARNING(f'Modality already exists: {modality.name}'))
                for key, value in modality_data.items():
                    if key != 'slug':
                        setattr(modality, key, value)
                modality.save()
                self.stdout.write(self.style.SUCCESS(f'Updated modality: {modality.name}'))

            project.modalities.add(modality)
            self.stdout.write(self.style.SUCCESS(f'Linked {modality.name} to {project.name} project'))

        self.stdout.write(self.style.SUCCESS(f'\nSuccessfully configured Dermatology project with {len(modalities_data)} modality'))