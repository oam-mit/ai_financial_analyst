from django.db import models
from django.contrib.auth.models import User

class Stock(models.Model):
    symbol = models.CharField(max_length=10, unique=True)
    company_name = models.CharField(max_length=255, blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.symbol

class SECFiling(models.Model):
    FILING_TYPES = (
        ('10-K', 'Annual Report'),
        ('10-Q', 'Quarterly Report'),
    )
    stock = models.ForeignKey(Stock, on_delete=models.CASCADE, related_name='filings')
    filing_type = models.CharField(max_length=10, choices=FILING_TYPES)
    filing_date = models.DateField()
    content_path = models.TextField()  # Path to stored file or text content
    accession_number = models.CharField(max_length=50, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.stock.symbol} - {self.filing_type} - {self.filing_date}"

class ChatSession(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='chat_sessions')
    stock = models.ForeignKey(Stock, on_delete=models.CASCADE, related_name='chat_sessions')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-updated_at']

    def __str__(self):
        return f"Chat with {self.user.username} on {self.stock.symbol}"

class ChatMessage(models.Model):
    session = models.ForeignKey(ChatSession, on_delete=models.CASCADE, related_name='messages')
    role = models.CharField(max_length=10, choices=(('user', 'User'), ('assistant', 'Assistant')))
    content = models.TextField()
    is_analysis = models.BooleanField(default=False)  # If this message is the deep dive analysis
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['created_at']

    def __str__(self):
        return f"{self.role}: {self.content[:50]}..."
