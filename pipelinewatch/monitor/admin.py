from django.contrib import admin

from .models import Build, Project


@admin.register(Project)
class ProjectAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "repo_url", "notify_email")
    prepopulated_fields = {"slug": ("name",)}
    search_fields = ("name", "slug")


@admin.register(Build)
class BuildAdmin(admin.ModelAdmin):
    list_display = ("project", "external_id", "branch", "short_sha", "status", "provider", "created_at")
    list_filter = ("status", "provider", "project")
    search_fields = ("commit_message", "author", "commit_sha", "branch")
    date_hierarchy = "created_at"
