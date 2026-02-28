from rest_framework import serializers
from django.contrib.auth.models import User
from .models import Stock, SECFiling, ChatSession, ChatMessage

class UserSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True)

    class Meta:
        model = User
        fields = ('username', 'password', 'email')

    def create(self, validated_data):
        user = User.objects.create_user(
            username=validated_data['username'],
            email=validated_data.get('email', ''),
            password=validated_data['password']
        )
        return user

class StockSerializer(serializers.ModelSerializer):
    class Meta:
        model = Stock
        fields = '__all__'

class SECFilingSerializer(serializers.ModelSerializer):
    class Meta:
        model = SECFiling
        fields = '__all__'

class ChatMessageSerializer(serializers.ModelSerializer):
    class Meta:
        model = ChatMessage
        fields = '__all__'

class ChatSessionSerializer(serializers.ModelSerializer):
    messages = ChatMessageSerializer(many=True, read_only=True)
    stock_symbol = serializers.ReadOnlyField(source='stock.symbol')
    company_name = serializers.ReadOnlyField(source='stock.company_name')

    class Meta:
        model = ChatSession
        fields = ('id', 'user', 'stock', 'stock_symbol', 'company_name', 'messages', 'created_at', 'updated_at')
