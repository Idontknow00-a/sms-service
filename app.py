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
    datefmt='H:%M:%S'
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
    """Obtém o preço do serviço"""
    try:
        current_time = time.time()
        if current_time - price_cache['time'] < 30 and SERVICE in price_cache['prices']:
            return price_cache['prices'][SERVICE]
        
        url = f"{BASE_URL}?api_key={API_KEY}&action=getPrices&service={SERVICE}&country={COUNTRY_CODE}"
        response = requests.get(url, timeout=10)
        
        if response.status_code == 200:
            data = response.json()
            logger.info(f"Preços raw: {data}")
            
            price = extract_price_from_response(data)
            
            if price > 0:
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
        if isinstance(data, list) and len(data) > 0:
            for item in data:
                if SERVICE in item and isinstance(item[SERVICE], dict):
                    if "cost" in item[SERVICE]:
                        return float(item[SERVICE]["cost"])
        
        if isinstance(data, dict):
            if SERVICE in data and isinstance(data[SERVICE], dict):
                if "cost" in data[SERVICE]:
                    return float(data[SERVICE]["cost"])
            
            if str(COUNTRY_CODE) in data and SERVICE in data[str(COUNTRY_CODE)]:
                service_info = data[str(COUNTRY_CODE)][SERVICE]
                if "cost" in service_info:
                    return float(service_info["cost"])
        
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
    """Obtém um número"""
    try:
        service_price = get_service_price()
        
        balance = get_balance()
        if balance < service_price + 0.01:
            logger.error(f"✗ Saldo insuficiente! Precisa: ${service_price:.4f}, Tem: ${balance:.4f}")
            return 'NO_BALANCE', "0.0000"
        
        url = f"{BASE_URL}?api_key={API_KEY}&action=getNumber&service={SERVICE}&country={COUNTRY_CODE}"
        response = requests.get(url, timeout=10)
        
        if response.status_code == 200:
            data = response.text.strip()
            
            if data.startswith('ACCESS_NUMBER'):
                can_get_another = '1'
                if ':' in data:
                    parts = data.split(':')
                    if len(parts) >= 4:
                        can_get_another = parts[3] if len(parts) > 3 else '1'
                
                logger.info(f"✓ Número obtido por ${service_price:.4f} | Multi-SMS: {'Sim' if can_get_another == '1' else 'Não'}")
                
                formatted_price = f"{service_price:.4f}"
                return data, formatted_price, can_get_another
            elif 'NO_NUMBERS' in data:
                logger.info("✗ Sem números disponíveis")
                return 'NO_NUMBERS', "0.0000", '0'
            elif 'NO_BALANCE' in data:
                logger.error("✗ Saldo insuficiente na API!")
                return 'NO_BALANCE', "0.0000", '0'
            else:
                logger.warning(f"Resposta inesperada: {data}")
                return data, "0.0000", '0'
        
        return 'NO_NUMBER', "0.0000", '0'
        
    except Exception as e:
        logger.error(f"Erro ao obter número: {e}")
        return 'NO_NUMBER', "0.0000", '0'

def cancel_number_automatically(number_id):
    """Cancela número automaticamente após timeout - APENAS SE NÃO RECEBEU CÓDIGO"""
    try:
        if number_id in active_numbers:
            num_info = active_numbers[number_id]
            received_codes = num_info.get('received_codes', [])
            
            if len(received_codes) > 0:
                logger.info(f"⚠️ Número {number_id} já recebeu {len(received_codes)} código(s). Cancelamento automático ignorado.")
                if number_id in number_timeouts:
                    del number_timeouts[number_id]
                return
        
        if number_id in number_timeouts:
            del number_timeouts[number_id]
        
        if number_id in active_numbers:
            del active_numbers[number_id]
        
        url = f"{BASE_URL}?api_key={API_KEY}&action=setStatus&status=8&id={number_id}"
        response = requests.get(url, timeout=5)
        logger.info(f"⏰ Número {number_id} cancelado automaticamente (timeout sem código)")
    except Exception as e:
        logger.error(f"Erro ao cancelar número {number_id}: {e}")

def setup_timeout(number_id):
    """Configura timeout para cancelamento automático"""
    timer = Timer(TIMEOUT_DURATION, lambda: cancel_number_automatically(number_id))
    timer.start()
    number_timeouts[number_id] = timer
    return timer

def stop_timeout(number_id):
    """Para o timeout do número (quando recebe código)"""
    try:
        if number_id in number_timeouts:
            number_timeouts[number_id].cancel()
            del number_timeouts[number_id]
            logger.info(f"⏹️ Timeout parado para {number_id} (código recebido)")
            return True
    except Exception as e:
        logger.error(f"Erro ao parar timeout: {e}")
    return False

def request_another_sms(number_id):
    """Solicita outro SMS para o mesmo número (status=3)"""
    try:
        url = f"{BASE_URL}?api_key={API_KEY}&action=setStatus&status=3&id={number_id}"
        response = requests.get(url, timeout=5)
        logger.info(f"📱 Novo SMS solicitado para {number_id}: {response.text}")
        return response.text
    except Exception as e:
        logger.error(f"Erro ao solicitar novo SMS: {e}")
        return None

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
        balance = get_balance()
        logger.info(f"💰 Saldo atual: ${balance:.4f}")
        
        if balance < 0.01:
            logger.error("✗ SALDO INSUFICIENTE!")
            return jsonify({
                'success': False,
                'response': 'NO_BALANCE',
                'message': f'Saldo insuficiente! Saldo: ${balance:.4f}'
            })
        
        data, price, can_get_another = get_number()
        
        if data.startswith('ACCESS_NUMBER'):
            parts = data.split(':')
            if len(parts) >= 3:
                number_id = parts[1].strip()
                phone_number = parts[2].strip()
                
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
                    'received_codes': [],
                    'can_get_another_sms': can_get_another == '1',
                    'sms_count': 0,
                    'last_activity': time.time(),
                    'last_code': None,
                    'last_code_time': None,
                    'has_received_code': False,
                    'waiting_for_new_code': False  # NOVO: flag para controlar espera de novo código
                }
                
                logger.info(f"✅ Número {display_number} obtido (ID: {number_id}, Preço: {price}, Multi-SMS: {can_get_another})")
                
                return jsonify({
                    'success': True,
                    'response': data,
                    'number_id': number_id,
                    'phone_number': display_number,
                    'full_number': phone_number,
                    'price': price,
                    'numeric_price': float(price),
                    'can_get_another_sms': can_get_another == '1',
                    'message': 'Número obtido com sucesso'
                })
        
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
            'status': 'waiting',
            'sms_count': 0,
            'can_get_more': True,
            'has_received_code': False,
            'waiting_for_new_code': False
        }
        
        if number_id not in active_numbers:
            result['success'] = False
            result['message'] = 'Número não encontrado ou expirado'
            result['status'] = 'expired'
            return jsonify(result)
        
        active_numbers[number_id]['last_activity'] = time.time()
        result['has_received_code'] = active_numbers[number_id].get('has_received_code', False)
        result['waiting_for_new_code'] = active_numbers[number_id].get('waiting_for_new_code', False)
        
        if data.startswith('STATUS_OK:'):
            code = data.split(':', 1)[1].strip()
            
            if number_id not in successful_numbers:
                successful_numbers.add(number_id)
            
            # Verifica se é um código novo
            existing_codes = [c['code'] for c in active_numbers[number_id].get('received_codes', [])]
            is_new_code = code not in existing_codes
            
            if is_new_code:
                if number_id in active_numbers:
                    active_numbers[number_id]['received_codes'].append({
                        'code': code,
                        'timestamp': time.time(),
                        'sms_number': active_numbers[number_id]['sms_count'] + 1
                    })
                    active_numbers[number_id]['status'] = 'code_received'
                    active_numbers[number_id]['last_code'] = code
                    active_numbers[number_id]['last_code_time'] = time.time()
                    active_numbers[number_id]['sms_count'] += 1
                    active_numbers[number_id]['has_received_code'] = True
                    active_numbers[number_id]['waiting_for_new_code'] = False  # Resetar flag
                    
                    stop_timeout(number_id)
                    
                    result['sms_count'] = active_numbers[number_id]['sms_count']
                    result['has_received_code'] = True
                    result['is_new_code'] = True
                    
                    can_get_more = active_numbers[number_id]['can_get_another_sms']
                    result['can_get_more'] = can_get_more
                    
                    if can_get_more:
                        request_result = request_another_sms(number_id)
                        active_numbers[number_id]['waiting_for_new_code'] = True  # Agora está aguardando novo código
                        result['next_sms_requested'] = True
                        result['request_response'] = request_result
                        result['waiting_for_new_code'] = True
                        logger.info(f"✅ Código {active_numbers[number_id]['sms_count']} recebido para {number_id}: {code} | Próximo SMS solicitado")
                    else:
                        logger.info(f"✅ Código {active_numbers[number_id]['sms_count']} recebido para {number_id}: {code}")
            else:
                # Código duplicado - retorna o último diferente
                result['sms_count'] = active_numbers[number_id]['sms_count']
                result['can_get_more'] = active_numbers[number_id]['can_get_another_sms']
                result['is_new_code'] = False
                logger.info(f"⚠️ Código duplicado detectado para {number_id}: {code}")
            
            result['has_code'] = True
            result['code'] = code
            result['status'] = 'received'
        
        elif data == 'STATUS_WAIT_CODE':
            result['message'] = 'Aguardando código...'
            result['status'] = 'waiting_code'
            if number_id in active_numbers:
                result['sms_count'] = active_numbers[number_id]['sms_count']
                result['can_get_more'] = active_numbers[number_id]['can_get_another_sms']
                result['has_received_code'] = active_numbers[number_id]['has_received_code']
                result['waiting_for_new_code'] = active_numbers[number_id].get('waiting_for_new_code', False)
            
            logger.info(f"⏳ Aguardando código para {number_id} (SMS #{result['sms_count'] + 1})")
        
        elif data == 'STATUS_CANCEL':
            result['message'] = 'Número cancelado'
            result['status'] = 'cancelled'
            logger.info(f"✗ Número {number_id} cancelado")
            
            if number_id in active_numbers:
                del active_numbers[number_id]
        
        else:
            result['message'] = data
            result['status'] = 'unknown'
            if number_id in active_numbers:
                result['sms_count'] = active_numbers[number_id]['sms_count']
                result['can_get_more'] = active_numbers[number_id]['can_get_another_sms']
                result['has_received_code'] = active_numbers[number_id]['has_received_code']
                result['waiting_for_new_code'] = active_numbers[number_id].get('waiting_for_new_code', False)
        
        return jsonify(result)
        
    except Exception as e:
        logger.error(f"Erro em /get_status: {e}")
        return jsonify({
            'success': False,
            'message': f'Erro: {str(e)}'
        }), 500

# ROTA CORRIGIDA: Para pegar e copiar o último código
@app.route('/get_last_code/<number_id>', methods=['GET'])
def get_last_code(number_id):
    """Retorna o último código recebido - IMEDIATO E SEM POLLING"""
    try:
        if number_id not in active_numbers:
            return jsonify({
                'success': False,
                'message': 'Número não encontrado',
                'code': None
            })
        
        received_codes = active_numbers[number_id].get('received_codes', [])
        
        if not received_codes:
            return jsonify({
                'success': False,
                'message': 'Nenhum código recebido ainda',
                'code': None,
                'should_wait': True  # Indica que ainda está aguardando
            })
        
        # Pega o último código
        last_code_info = received_codes[-1]
        last_code = last_code_info['code']
        
        logger.info(f"📋 Último código retornado para {number_id}: {last_code}")
        
        return jsonify({
            'success': True,
            'code': last_code,
            'sms_number': last_code_info.get('sms_number', 1),
            'timestamp': last_code_info['timestamp'],
            'time_formatted': time.strftime('%H:%M:%S', time.localtime(last_code_info['timestamp'])),
            'total_codes': len(received_codes),
            'message': f'Código #{last_code_info.get("sms_number", 1)} obtido',
            'should_wait': False,  # NÃO deve continuar aguardando
            'waiting_for_new_code': active_numbers[number_id].get('waiting_for_new_code', False)
        })
        
    except Exception as e:
        logger.error(f"Erro em /get_last_code: {e}")
        return jsonify({
            'success': False,
            'message': f'Erro: {str(e)}',
            'code': None
        }), 500

@app.route('/get_all_codes/<number_id>', methods=['GET'])
def get_all_codes(number_id):
    """Retorna todos os códigos recebidos"""
    try:
        if number_id not in active_numbers:
            return jsonify({
                'success': False,
                'message': 'Número não encontrado'
            })
        
        received_codes = active_numbers[number_id].get('received_codes', [])
        
        formatted_codes = []
        for code_info in received_codes:
            formatted_codes.append({
                'code': code_info['code'],
                'sms_number': code_info.get('sms_number', 1),
                'timestamp': code_info['timestamp'],
                'time_formatted': time.strftime('%H:%M:%S', time.localtime(code_info['timestamp']))
            })
        
        return jsonify({
            'success': True,
            'total_codes': len(received_codes),
            'codes': formatted_codes,
            'last_code': formatted_codes[-1]['code'] if formatted_codes else None,
            'message': f'{len(received_codes)} códigos encontrados'
        })
        
    except Exception as e:
        logger.error(f"Erro em /get_all_codes: {e}")
        return jsonify({
            'success': False,
            'message': f'Erro: {str(e)}'
        }), 500

@app.route('/request_another_sms/<number_id>', methods=['GET'])
def route_request_another_sms(number_id):
    """Solicita manualmente outro SMS para o mesmo número"""
    try:
        if number_id not in active_numbers:
            return jsonify({
                'success': False,
                'message': 'Número não encontrado'
            })
        
        if not active_numbers[number_id]['can_get_another_sms']:
            return jsonify({
                'success': False,
                'message': 'Este número não suporta múltiplos SMS'
            })
        
        response_text = request_another_sms(number_id)
        
        active_numbers[number_id]['last_activity'] = time.time()
        active_numbers[number_id]['waiting_for_new_code'] = True  # Marca que está aguardando
        
        return jsonify({
            'success': True,
            'response': response_text,
            'message': 'Novo SMS solicitado com sucesso',
            'waiting_for_new_code': True
        })
        
    except Exception as e:
        logger.error(f"Erro em /request_another_sms: {e}")
        return jsonify({
            'success': False,
            'message': f'Erro: {str(e)}'
        }), 500

@app.route('/cancel_number/<number_id>', methods=['GET'])
def route_cancel_number(number_id):
    """Cancela número manualmente"""
    try:
        if number_id in number_timeouts:
            number_timeouts[number_id].cancel()
            del number_timeouts[number_id]
        
        if number_id in active_numbers:
            del active_numbers[number_id]
        
        if number_id in successful_numbers:
            successful_numbers.remove(number_id)
        
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
    """Finaliza número (status=6)"""
    try:
        url = f"{BASE_URL}?api_key={API_KEY}&action=setStatus&status=6&id={number_id}"
        response = requests.get(url, timeout=5)
        
        if number_id in number_timeouts:
            number_timeouts[number_id].cancel()
            del number_timeouts[number_id]
        
        if number_id in active_numbers:
            if number_id not in successful_numbers:
                successful_numbers.add(number_id)
            del active_numbers[number_id]
        
        logger.info(f"🏁 Número {number_id} finalizado: {response.text}")
        
        return jsonify({
            'success': True,
            'response': response.text,
            'message': 'Número finalizado com sucesso'
        })
        
    except Exception as e:
        logger.error(f"Erro em /finish_number: {e}")
        return jsonify({
            'success': False,
            'message': f'Erro: {str(e)}'
        }), 500

@app.route('/number_info/<number_id>', methods=['GET'])
def route_number_info(number_id):
    """Retorna informações detalhadas sobre o número"""
    try:
        if number_id not in active_numbers:
            return jsonify({
                'success': False,
                'message': 'Número não encontrado'
            })
        
        number_info = active_numbers[number_id].copy()
        number_info['created_at_formatted'] = time.strftime('%H:%M:%S', time.localtime(number_info['created_at']))
        number_info['age_seconds'] = int(time.time() - number_info['created_at'])
        number_info['last_activity_formatted'] = time.strftime('%H:%M:%S', time.localtime(number_info['last_activity']))
        number_info['inactive_seconds'] = int(time.time() - number_info['last_activity'])
        
        if number_info.get('last_code'):
            number_info['last_code_time_formatted'] = time.strftime('%H:%M:%S', time.localtime(number_info.get('last_code_time', 0)))
        
        return jsonify({
            'success': True,
            'number_info': number_info
        })
        
    except Exception as e:
        logger.error(f"Erro em /number_info: {e}")
        return jsonify({
            'success': False,
            'message': f'Erro: {str(e)}'
        }), 500

@app.route('/stats', methods=['GET'])
def route_stats():
    """Estatísticas"""
    balance = get_balance()
    
    total_codes = 0
    total_sms = 0
    multi_sms_numbers = 0
    
    for num_id, num_info in active_numbers.items():
        if 'received_codes' in num_info:
            total_codes += len(num_info['received_codes'])
            total_sms += num_info.get('sms_count', 0)
            if num_info.get('sms_count', 0) > 1:
                multi_sms_numbers += 1
    
    current_price = get_service_price()
    
    return jsonify({
        'success': True,
        'successful_numbers': len(successful_numbers),
        'active_numbers': len(active_numbers),
        'total_codes': total_codes,
        'total_sms': total_sms,
        'multi_sms_numbers': multi_sms_numbers,
        'balance': f"${balance:.4f}",
        'current_price': f"${current_price:.4f}",
        'balance_numeric': balance,
        'price_numeric': current_price,
        'timeout_duration': TIMEOUT_DURATION
    })

if __name__ == '__main__':
    logger.info("🚀 Servidor HeroSMS OTIMIZADO iniciado")
    logger.info(f"🌎 País: {COUNTRY_CODE}")
    logger.info(f"📱 Serviço: {SERVICE}")
    logger.info(f"⏰ Timeout: {TIMEOUT_DURATION}s")
    print("\n" + "="*50)
    
    try:
        balance = get_balance()
        price = get_service_price()
        logger.info(f"💰 Saldo: ${balance:.4f}")
        logger.info(f"💵 Preço do serviço: ${price:.4f}")
    except:
        logger.warning("⚠️ Não foi possível verificar saldo/preço")
    
    app.run(debug=True, port=3000, host='0.0.0.0')
