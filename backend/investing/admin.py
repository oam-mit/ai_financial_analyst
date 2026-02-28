from django.contrib import admin
from .models import Stock, SECFiling, ChatSession, ChatMessage

@admin.register(Stock)
class StockAdmin(admin.ModelAdmin):
    list_display = ('symbol', 'company_name', 'created_at')
    search_fields = ('symbol', 'company_name')

@admin.register(SECFiling)
class SECFilingAdmin(admin.ModelAdmin):
    list_display = ('stock', 'filing_type', 'filing_date', 'accession_number')
    list_filter = ('filing_type', 'filing_date')
    search_fields = ('stock__symbol', 'accession_number')

@admin.register(ChatSession)
class ChatSessionAdmin(admin.ModelAdmin):
    list_display = ('id', 'user', 'stock', 'updated_at')
    list_filter = ('user', 'stock')
    search_fields = ('user__username', 'stock__symbol')

@admin.register(ChatMessage)
class ChatMessageAdmin(admin.ModelAdmin):
    list_display = ('id', 'session', 'role', 'is_analysis', 'created_at')
    list_filter = ('role', 'is_analysis')
    search_fields = ('content', 'session__user__username', 'session__stock__symbol')
