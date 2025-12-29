from flask import Flask, jsonify, render_template
from flask_cors import CORS
import requests
import time
from threading import Timer
import logging
import os

app = Flask(__name__)
CORS(app)

# Configuração
API_KEY = '8f0d024fA7f44b1efd86AAe1180f5590'
COUNTRY_CODE = 73  # Brasil
SERVICE = 'mm'  # WhatsApp
TIMEOUT_DURATION = 120  # segundos

# Armazenamento em memória
number_timeouts = {}
active_numbers = {}
successful_numbers = set()

# Configurar logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(message)s',
    datefmt='%H:%M:%S'
)
logger = logging.getLogger(__name__)

BASE_URL = "https://hero-sms.com/stubs/handler_api.php"

# Cache de preços
price_cache = {'time': 0, 'prices': {}}

def get_balance():
    """Obtém o saldo da conta"""
    try:
        url = f"{BASE_URL}?api_key={API_KEY}&action=getBalance"
        response = requests.get(url, timeout=10)
        
        if response.status_code == 200:
            data = response.text.strip()
            if data.startswith('ACCESS_BALANCE:'):
                balance = float(data.split(':')[1])
                return balance
        return 0.0
    except Exception as e:
        logger.error(f"Erro ao obter saldo: {e}")
        return 0.0

def get_service_price():
    """Obtém o preço do serviço - VERSÃO SIMPLIFICADA"""
    try:
        # Verifica cache (válido por 30 segundos)
        current_time = time.time()
        if current_time - price_cache['time'] < 30 and SERVICE in price_cache['prices']:
            return price_cache['prices'][SERVICE]
        
        # URL para preços da HeroSMS
        url = f"{BASE_URL}?api_key={API_KEY}&action=getPrices&service={SERVICE}&country={COUNTRY_CODE}"
        response = requests.get(url, timeout=10)
        
        if response.status_code == 200:
            data = response.json()
            
            # Log para debug
            logger.info(f"Preços raw: {data}")
            
            # Tenta extrair preço de diferentes estruturas
            price = extract_price_from_response(data)
            
            if price > 0:
                # Atualiza cache
                price_cache['time'] = current_time
                price_cache['prices'][SERVICE] = price
                logger.info(f"💰 Preço do serviço {SERVICE}: ${price:.4f}")
                return price
        
        return 0.0
        
    except Exception as e:
        logger.error(f"Erro ao obter preço: {e}")
        return 0.0

def extract_price_from_response(data):
    """Extrai preço da resposta da API"""
    try:
        # Estrutura 1: Lista de objetos
        if isinstance(data, list) and len(data) > 0:
            for item in data:
                if SERVICE in item and isinstance(item[SERVICE], dict):
                    if "cost" in item[SERVICE]:
                        return float(item[SERVICE]["cost"])
        
        # Estrutura 2: Dicionário direto
        if isinstance(data, dict):
            # Verifica se tem o serviço como chave
            if SERVICE in data and isinstance(data[SERVICE], dict):
                if "cost" in data[SERVICE]:
                    return float(data[SERVICE]["cost"])
            
            # Verifica se tem a estrutura country->service
            if str(COUNTRY_CODE) in data and SERVICE in data[str(COUNTRY_CODE)]:
                service_info = data[str(COUNTRY_CODE)][SERVICE]
                if "cost" in service_info:
                    return float(service_info["cost"])
        
        # Tenta usar getTopCountriesByService como fallback
        fallback_price = get_price_fallback()
        if fallback_price > 0:
            return fallback_price
            
    except Exception as e:
        logger.error(f"Erro ao extrair preço: {e}")
    
    return 0.0

def get_price_fallback():
    """Método alternativo para obter preço"""
    try:
        url = f"{BASE_URL}?api_key={API_KEY}&action=getTopCountriesByService&service={SERVICE}"
        response = requests.get(url, timeout=10)
        
        if response.status_code == 200:
            data = response.json()
            
            # Procura pelo país 73 (Brasil) na resposta
            if isinstance(data, list):
                for item in data:
                    for service_key, countries in item.items():
                        if service_key == SERVICE:
                            for country_info in countries:
                                if country_info.get('country') == COUNTRY_CODE:
                                    return float(country_info.get('price', 0))
    except:
        pass
    
    return 0.0

def get_number():
    """Obtém um número - VERSÃO SIMPLIFICADA E FUNCIONAL"""
    try:
        # Obtém preço primeiro
        service_price = get_service_price()
        
        # Verifica saldo
        balance = get_balance()
        if balance < service_price + 0.01:  # Margem de segurança
            logger.error(f"✗ Saldo insuficiente! Precisa: ${service_price:.4f}, Tem: ${balance:.4f}")
            return 'NO_BALANCE', "0.0000"
        
        # Obtém número
        url = f"{BASE_URL}?api_key={API_KEY}&action=getNumber&service={SERVICE}&country={COUNTRY_CODE}"
        response = requests.get(url, timeout=10)
        
        if response.status_code == 200:
            data = response.text.strip()
            
            if data.startswith('ACCESS_NUMBER'):
                formatted_price = f"{service_price:.4f}"
                logger.info(f"✓ Número obtido por ${formatted_price}")
                return data, formatted_price
            elif 'NO_NUMBERS' in data:
                logger.info("✗ Sem números disponíveis")
                return 'NO_NUMBERS', "0.0000"
            elif 'NO_BALANCE' in data:
                logger.error("✗ Saldo insuficiente na API!")
                return 'NO_BALANCE', "0.0000"
            else:
                logger.warning(f"Resposta inesperada: {data}")
                return data, "0.0000"
        
        return 'NO_NUMBER', "0.0000"
        
    except Exception as e:
        logger.error(f"Erro ao obter número: {e}")
        return 'NO_NUMBER', "0.0000"

def cancel_number_automatically(number_id):
    """Cancela número automaticamente após timeout"""
    try:
        if number_id in number_timeouts:
            del number_timeouts[number_id]
        
        if number_id in active_numbers:
            del active_numbers[number_id]
        
        # Cancela na API
        url = f"{BASE_URL}?api_key={API_KEY}&action=setStatus&status=8&id={number_id}"
        response = requests.get(url, timeout=5)
        logger.info(f"⏰ Número {number_id} cancelado automaticamente (timeout)")
    except Exception as e:
        logger.error(f"Erro ao cancelar número {number_id}: {e}")

def setup_timeout(number_id):
    """Configura timeout para cancelamento automático"""
    timer = Timer(TIMEOUT_DURATION, lambda: cancel_number_automatically(number_id))
    timer.start()
    number_timeouts[number_id] = timer
    return timer

# Rotas da API
@app.route('/')
def index():
    return render_template('index.html')

@app.route('/get_price', methods=['GET'])
def get_price():
    """Rota específica para obter preço atual"""
    try:
        price = get_service_price()
        return jsonify({
            'success': True,
            'price': f"${price:.4f}",
            'numeric_price': price
        })
    except Exception as e:
        logger.error(f"Erro em /get_price: {e}")
        return jsonify({
            'success': False,
            'price': "$0.0000",
            'message': str(e)
        })

@app.route('/get_number', methods=['GET'])
def route_get_number():
    """Obtém um novo número"""
    try:
        # Verifica saldo primeiro
        balance = get_balance()
        logger.info(f"💰 Saldo atual: ${balance:.4f}")
        
        if balance < 0.01:
            logger.error("✗ SALDO INSUFICIENTE!")
            return jsonify({
                'success': False,
                'response': 'NO_BALANCE',
                'message': f'Saldo insuficiente! Saldo: ${balance:.4f}'
            })
        
        # Obtém número
        data, price = get_number()
        
        if data.startswith('ACCESS_NUMBER'):
            parts = data.split(':')
            if len(parts) >= 3:
                number_id = parts[1].strip()
                phone_number = parts[2].strip()
                
                # Formata número para exibição
                if phone_number.startswith('55') and len(phone_number) > 10:
                    display_number = phone_number[2:]
                else:
                    display_number = phone_number
                
                setup_timeout(number_id)
                
                active_numbers[number_id] = {
                    'phone_number': display_number,
                    'full_number': phone_number,
                    'price': price,
                    'status': 'waiting',
                    'created_at': time.time(),
                    'received_codes': []
                }
                
                logger.info(f"✅ Número {display_number} obtido (ID: {number_id}, Preço: {price})")
                
                return jsonify({
                    'success': True,
                    'response': data,
                    'number_id': number_id,
                    'phone_number': display_number,
                    'full_number': phone_number,
                    'price': price,
                    'numeric_price': float(price),
                    'message': 'Número obtido com sucesso'
                })
        
        # Tratamento de erros
        error_message = "Não foi possível obter número"
        if 'NO_NUMBERS' in data:
            error_message = "Sem números disponíveis no momento"
        elif 'NO_BALANCE' in data:
            error_message = "Saldo insuficiente"
        
        return jsonify({
            'success': False,
            'response': data,
            'message': error_message
        })
        
    except Exception as e:
        logger.error(f"Erro em /get_number: {e}")
        return jsonify({
            'success': False,
            'message': f'Erro interno: {str(e)}'
        }), 500

@app.route('/get_status/<number_id>', methods=['GET'])
def route_get_status(number_id):
    """Verifica status do número"""
    try:
        url = f"{BASE_URL}?api_key={API_KEY}&action=getStatus&id={number_id}"
        response = requests.get(url, timeout=10)
        data = response.text.strip()
        
        logger.info(f"Status check para {number_id}: {data}")
        
        result = {
            'success': True,
            'response': data,
            'has_code': False,
            'code': None,
            'status': 'waiting'
        }
        
        if data.startswith('STATUS_OK:'):
            # Código recebido
            code = data.split(':', 1)[1].strip()
            
            # Cancelar timeout
            if number_id in number_timeouts:
                number_timeouts[number_id].cancel()
                del number_timeouts[number_id]
            
            # Adicionar aos sucessos
            if number_id not in successful_numbers:
                successful_numbers.add(number_id)
                logger.info(f"✅ Primeiro código recebido para {number_id}")
            
            # Armazenar código
            if number_id in active_numbers:
                active_numbers[number_id]['received_codes'].append(code)
                active_numbers[number_id]['status'] = 'code_received'
                active_numbers[number_id]['last_code'] = code
            
            # Solicitar novo SMS (opcional)
            try:
                retry_url = f"{BASE_URL}?api_key={API_KEY}&action=setStatus&status=3&id={number_id}"
                retry_response = requests.get(retry_url, timeout=5)
                logger.info(f"📱 Novo SMS solicitado: {retry_response.text}")
            except Exception as e:
                logger.error(f"Erro ao solicitar novo SMS: {e}")
            
            logger.info(f"✅ Código recebido para {number_id}: {code}")
            
            result['has_code'] = True
            result['code'] = code
            result['status'] = 'received'
        
        elif data == 'STATUS_WAIT_CODE':
            result['message'] = 'Aguardando código...'
            result['status'] = 'waiting_code'
            logger.info(f"⏳ Aguardando código para {number_id}")
        
        elif data == 'STATUS_CANCEL':
            result['message'] = 'Número cancelado'
            result['status'] = 'cancelled'
            logger.info(f"✗ Número {number_id} cancelado")
            
            if number_id in active_numbers:
                del active_numbers[number_id]
        
        else:
            result['message'] = data
            result['status'] = 'unknown'
        
        return jsonify(result)
        
    except Exception as e:
        logger.error(f"Erro em /get_status: {e}")
        return jsonify({
            'success': False,
            'message': f'Erro: {str(e)}'
        }), 500

@app.route('/cancel_number/<number_id>', methods=['GET'])
def route_cancel_number(number_id):
    """Cancela número manualmente"""
    try:
        # Cancela timeout
        if number_id in number_timeouts:
            number_timeouts[number_id].cancel()
            del number_timeouts[number_id]
        
        # Remove de ativos
        if number_id in active_numbers:
            del active_numbers[number_id]
        
        # Remove de sucessos
        if number_id in successful_numbers:
            successful_numbers.remove(number_id)
        
        # Cancela na API
        url = f"{BASE_URL}?api_key={API_KEY}&action=setStatus&status=8&id={number_id}"
        response = requests.get(url, timeout=5)
        
        logger.info(f"✗ Número {number_id} cancelado: {response.text}")
        
        return jsonify({
            'success': True,
            'response': response.text,
            'message': 'Número cancelado'
        })
        
    except Exception as e:
        logger.error(f"Erro em /cancel_number: {e}")
        return jsonify({
            'success': False,
            'message': f'Erro: {str(e)}'
        }), 500

@app.route('/finish_number/<number_id>', methods=['GET'])
def route_finish_number(number_id):
    """Finaliza número"""
    try:
        url = f"{BASE_URL}?api_key={API_KEY}&action=setStatus&status=6&id={number_id}"
        response = requests.get(url, timeout=5)
        
        # Limpa recursos
        if number_id in number_timeouts:
            number_timeouts[number_id].cancel()
            del number_timeouts[number_id]
        
        if number_id in active_numbers:
            del active_numbers[number_id]
        
        logger.info(f"🏁 Número {number_id} finalizado: {response.text}")
        
        return jsonify({
            'success': True,
            'response': response.text,
            'message': 'Número finalizado'
        })
        
    except Exception as e:
        logger.error(f"Erro em /finish_number: {e}")
        return jsonify({
            'success': False,
            'message': f'Erro: {str(e)}'
        }), 500

@app.route('/stats', methods=['GET'])
def route_stats():
    """Estatísticas"""
    balance = get_balance()
    
    total_codes = 0
    for num in active_numbers.values():
        if 'received_codes' in num:
            total_codes += len(num['received_codes'])
    
    # Obtém preço atual
    current_price = get_service_price()
    
    return jsonify({
        'success': True,
        'successful_numbers': len(successful_numbers),
        'active_numbers': len(active_numbers),
        'total_codes': total_codes,
        'balance': f"${balance:.4f}",
        'current_price': f"${current_price:.4f}",
        'balance_numeric': balance,
        'price_numeric': current_price
    })

if __name__ == '__main__':
    logger.info("🚀 Servidor HeroSMS iniciado")
    logger.info(f"🌎 País: {COUNTRY_CODE}")
    logger.info(f"📱 Serviço: {SERVICE}")
    logger.info(f"⏰ Timeout: {TIMEOUT_DURATION}s")
    print("\n" + "="*50)
    
    # Testa conexão e preço
    try:
        balance = get_balance()
        price = get_service_price()
        logger.info(f"💰 Saldo: ${balance:.4f}")
        logger.info(f"💵 Preço do serviço: ${price:.4f}")
    except:
        logger.warning("⚠️ Não foi possível verificar saldo/preço")
    
    app.run(debug=True, port=3000, host='0.0.0.0')
