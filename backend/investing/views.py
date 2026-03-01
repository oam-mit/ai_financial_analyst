from rest_framework import viewsets, status, permissions
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView
from django.shortcuts import get_object_or_404
from django.db import models
from asgiref.sync import async_to_sync, sync_to_async
from .models import Stock, SECFiling, ChatSession, ChatMessage
from .serializers import StockSerializer, SECFilingSerializer, ChatSessionSerializer, ChatMessageSerializer, UserSerializer
from .services import SECService, DCFService, LLMService, read_filing_content
from django.http import HttpResponse, StreamingHttpResponse
from django.conf import settings
import os
import requests

class RegisterView(APIView):
    permission_classes = [permissions.AllowAny]
    def post(self, request):
        serializer = UserSerializer(data=request.data)
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

class StockViewSet(viewsets.ModelViewSet):
    queryset = Stock.objects.all()
    serializer_class = StockSerializer
    lookup_field = 'symbol'

    @action(detail=True, methods=['post'])
    def fetch_filings(self, request, symbol=None):
        stock = self.get_object()
        sec_service = SECService()
        filings = sec_service.fetch_last_4_filings(stock)
        serializer = SECFilingSerializer(filings, many=True)
        return Response(serializer.data)

    @action(detail=False, methods=['get'])
    def search(self, request):
        query = request.query_params.get('q', '')
        if not query:
            return Response([])

        # 1. Search local DB
        local_stocks = Stock.objects.filter(
            models.Q(symbol__icontains=query) | 
            models.Q(company_name__icontains=query)
        )[:10]
        
        results = []
        seen_symbols = set()
        
        for s in local_stocks:
            results.append({'symbol': s.symbol, 'name': s.company_name or s.symbol})
            seen_symbols.add(s.symbol)

        # 2. Search Yahoo Finance API
        try:
            # Using Yahoo Finance suggest API
            url = f"https://query2.finance.yahoo.com/v1/finance/search?q={query}&quotesCount=10&newsCount=0"
            headers = {'User-Agent': 'Mozilla/5.0'}
            response = requests.get(url, headers=headers, timeout=5)
            if response.status_code == 200:
                data = response.json()
                for quote in data.get('quotes', []):
                    symbol = quote.get('symbol')
                    if symbol and symbol not in seen_symbols:
                        results.append({
                            'symbol': symbol,
                            'name': quote.get('longname') or quote.get('shortname') or symbol
                        })
                        seen_symbols.add(symbol)
        except Exception as e:
            # Log error or silently fail for external API
            print(f"Yahoo Search Error: {e}")

        return Response(results[:15])

    @action(detail=True, methods=['get'])
    def price(self, request, symbol=None):
        import yfinance as yf
        try:
            # use 'symbol' because lookup_field = 'symbol'
            stock = yf.Ticker(symbol)
            info = stock.info
            
            # Try different price keys as yfinance can be inconsistent
            current_price = info.get('currentPrice') or info.get('regularMarketPrice') or info.get('price')
            
            if current_price is None:
                try:
                    current_price = stock.fast_info.last_price
                except:
                    pass
            
            # Get change data
            change_percent = info.get('regularMarketChangePercent')
            change_value = info.get('regularMarketChange')
            
            # Fallback for change data
            if change_percent is None and current_price is not None:
                prev_close = info.get('previousClose') or info.get('regularMarketPreviousClose')
                if prev_close:
                    change_value = current_price - prev_close
                    change_percent = (change_value / prev_close) * 100

            return Response({
                'symbol': symbol, 
                'price': current_price,
                'change': change_value,
                'change_percent': change_percent
            })
        except Exception as e:
            return Response({'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    @action(detail=True, methods=['get'])
    def audio_clip(self, request, symbol=None):
        start = float(request.query_params.get('start', 0))
        end = float(request.query_params.get('end', 10))
        
        # Search for the audio file in possible locations
        possible_paths = [
            os.path.join(settings.BASE_DIR, "audio_files", symbol.upper(), "testing_call.mp3"),
            os.path.join(settings.BASE_DIR, "testing_call.mp3"),
        ]
        
        audio_path = None
        for path in possible_paths:
            if os.path.exists(path):
                audio_path = path
                break
                
        if not audio_path:
            return Response({"error": f"Audio file not found for {symbol}"}, status=status.HTTP_404_NOT_FOUND)
        
        try:
            from pydub import AudioSegment
            import io
            
            # This requires ffmpeg on the system
            audio = AudioSegment.from_mp3(audio_path)
            clip = audio[int(start * 1000):int(end * 1000)]
            
            buffer = io.BytesIO()
            clip.export(buffer, format="mp3")
            buffer.seek(0)
            
            response = HttpResponse(buffer.read(), content_type="audio/mp3")
            response['Content-Disposition'] = f'inline; filename="clip_{symbol}_{start}_{end}.mp3"'
            return response
        except Exception as e:
            # FALLBACK: Stream the entire file if ffmpeg is missing
            # The frontend can use start/end to control playback.
            # This is a robust fallback so the UI still works.
            try:
                with open(audio_path, 'rb') as f:
                    response = HttpResponse(f.read(), content_type="audio/mp3")
                    response['Content-Disposition'] = f'inline; filename="full_{symbol}.mp3"'
                    response['X-Is-Full-Audio'] = 'true' # Inform frontend it needs to handle slicing
                    return response
            except Exception as read_err:
                return Response({"error": f"Audio access failed: {str(read_err)}"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

from django.http import StreamingHttpResponse

class ChatSessionViewSet(viewsets.ModelViewSet):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = ChatSessionSerializer

    def get_queryset(self):
        return ChatSession.objects.filter(user=self.request.user)

    @action(detail=False, methods=['post'])
    def start_or_get_latest(self, request):
        symbol = request.data.get('symbol')
        if not symbol:
            return Response({"error": "Symbol is required"}, status=status.HTTP_400_BAD_REQUEST)
        
        stock, created = Stock.objects.get_or_create(symbol=symbol.upper())
        
        # Check if we have filings
        if stock.filings.count() == 0:
            sec_service = SECService()
            sec_service.fetch_last_4_filings(stock)

        session = ChatSession.objects.filter(user=request.user, stock=stock).order_by('-updated_at').first()
        
        if not session:
            session = ChatSession.objects.create(user=request.user, stock=stock)
        
        serializer = self.get_serializer(session)
        return Response(serializer.data)

    @action(detail=True, methods=['post'])
    def analyze(self, request, pk=None):
        session = self.get_object()
        stock = session.stock
        model_choice = request.data.get('model_choice', 'mistral')
        llm_service = LLMService(model_choice=model_choice)
        
        async def process_analysis():
            # 1. Main analysis
            full_text = await llm_service.get_analysis_v2(stock.symbol)
            
            # 2. Get highlights from transcript (fetch from DB)
            highlights = []
            if stock.transcript:
                try:
                    highlights = await llm_service.get_transcript_highlights(stock.transcript)
                except Exception as e:
                    print(f"Error processing transcript: {e}")

            # Save as message after complete
            await sync_to_async(ChatMessage.objects.create)(
                session=session,
                role='assistant',
                content=full_text,
                is_analysis=True,
                highlights=highlights
            )
            return {"content": full_text, "highlights": highlights}

        try:
            result = async_to_sync(process_analysis)()
            return Response(result, status=status.HTTP_200_OK)
        except Exception as e:
            return Response({"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    @action(detail=True, methods=['post'])
    def send_message(self, request, pk=None):
        session = self.get_object()
        user_content = request.data.get('content')
        if not user_content:
            return Response({"error": "Content is required"}, status=status.HTTP_400_BAD_REQUEST)
        
        # Save User Message
        ChatMessage.objects.create(session=session, role='user', content=user_content)
        
        # Get History for LLM
        messages = session.messages.all().order_by('created_at')
        history = [{"role": m.role, "content": m.content} for m in messages]
        
        # Use MCP-powered LLM Service
        model_choice = request.data.get('model_choice', 'mistral')
        llm_service = LLMService(model_choice=model_choice)
        
        async def process_message():
            full_text = await llm_service.get_chat_response_v2(history, user_content)
            await sync_to_async(ChatMessage.objects.create)(
                session=session, 
                role='assistant', 
                content=full_text
            )
            return full_text

        try:
            full_text = async_to_sync(process_message)()
            return Response({"content": full_text}, status=status.HTTP_200_OK)
        except Exception as e:
            return Response({"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
