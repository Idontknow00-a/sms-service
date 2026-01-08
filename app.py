from flask import Flask, jsonify, render_template
from flask_cors import CORS
import requests
import time
from threading import Timer, Thread
import logging
import os

app = Flask(__name__)
CORS(app)

# Configuração
API_KEY = '8f0d024fA7f44b1efd86AAe1180f5590'
COUNTRY_CODE = 73  # Brasil
SERVICE = 'mm'
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


def get_service_price_async(number_id):
    """Obtém o preço do serviço em background (não bloqueia)"""
    try:
        url = f"{BASE_URL}?api_key={API_KEY}&action=getPrices"
        response = requests.get(url, timeout=10)
        
        if response.status_code == 200:
            data = response.json()
            
            # Tenta extrair o preço
            if str(COUNTRY_CODE) in data and SERVICE in data[str(COUNTRY_CODE)]:
                service_info = data[str(COUNTRY_CODE)][SERVICE]
                if "cost" in service_info:
                    price = float(service_info["cost"])
                    formatted_price = f"{price:.4f}"
                    
                    # Atualiza o preço do número ativo
                    if number_id in active_numbers:
                        active_numbers[number_id]['price'] = formatted_price
                        logger.info(f"💰 Preço atualizado para {number_id}: ${formatted_price}")
                    
                    return formatted_price
    except Exception as e:
        logger.error(f"Erro ao obter preço em background: {e}")
    
    return "0.0000"


def get_number():
    """Obtém um número DIRETO - sem verificações prévias"""
    try:
        url = f"{BASE_URL}?api_key={API_KEY}&action=getNumber&service={SERVICE}&country={COUNTRY_CODE}"
        response = requests.get(url, timeout=10)
        
        if response.status_code == 200:
            data = response.text.strip()
            
            if data.startswith('ACCESS_NUMBER'):
                logger.info(f"✓ Número obtido com sucesso")
                return data, "..."  # Placeholder - será atualizado em background
            elif 'NO_NUMBERS' in data:
                logger.info("✗ Sem números disponíveis")
                return 'NO_NUMBERS', "0.0000"
            elif 'NO_BALANCE' in data:
                logger.error("✗ Saldo insuficiente!")
                return 'NO_BALANCE', "0.0000"
            elif 'BAD_KEY' in data:
                logger.error("✗ API Key inválida!")
                return 'BAD_KEY', "0.0000"
            else:
                logger.warning(f"Resposta inesperada: {data}")
                return data, "0.0000"
        
        return 'NO_NUMBER', "0.0000"
        
    except Exception as e:
        logger.error(f"Erro ao obter número: {e}")
        return 'NO_NUMBER', "0.0000"


def cancel_number_automatically(number_id):
    """Cancela número após timeout"""
    try:
        if number_id in number_timeouts:
            del number_timeouts[number_id]
        if number_id in active_numbers:
            del active_numbers[number_id]
        url = f"{BASE_URL}?api_key={API_KEY}&action=setStatus&status=8&id={number_id}"
        requests.get(url, timeout=5)
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


@app.route('/get_number', methods=['GET'])
def get_number_route():
    """Obtém novo número DIRETO - preço atualizado em background"""
    try:
        # Compra direto - 1 REQUISIÇÃO APENAS!
        data, price = get_number()
        
        if data.startswith('ACCESS_NUMBER'):
            parts = data.split(':', 2)
            number_id = parts[1].strip()
            phone_number = parts[2].strip().replace('55', '', 1)
            setup_timeout(number_id)
            active_numbers[number_id] = {
                'phone_number': phone_number,
                'price': price,
                'status': 'waiting',
                'created_at': time.time(),
                'received_codes': []
            }
            
            # Busca o preço em background (não bloqueia)
            Thread(target=get_service_price_async, args=(number_id,), daemon=True).start()
            
            logger.info(f"✅ Número {phone_number} obtido (ID: {number_id})")
            return jsonify({
                'success': True,
                'response': data,
                'number_id': number_id,
                'phone_number': phone_number,
                'price': price,
                'message': 'Número obtido com sucesso'
            })
        else:
            msg_map = {
                'NO_BALANCE': 'Saldo insuficiente!',
                'NO_NUMBERS': 'Sem números disponíveis',
                'NO_NUMBER': 'Falha ao obter número',
                'BAD_KEY': 'API Key inválida'
            }
            return jsonify({
                'success': False,
                'response': data,
                'message': msg_map.get(data, 'Erro desconhecido')
            })
    except Exception as e:
        logger.error(f"Erro em /get_number: {e}")
        return jsonify({'success': False, 'message': f'Erro interno: {str(e)}'}), 500


@app.route('/get_price/<number_id>', methods=['GET'])
def get_price(number_id):
    """Retorna o preço atualizado de um número específico"""
    try:
        if number_id in active_numbers:
            price = active_numbers[number_id].get('price', '0.0000')
            return jsonify({
                'success': True,
                'number_id': number_id,
                'price': price
            })
        else:
            return jsonify({
                'success': False,
                'message': 'Número não encontrado'
            })
    except Exception as e:
        logger.error(f"Erro em /get_price: {e}")
        return jsonify({'success': False, 'message': f'Erro: {str(e)}'}), 500


@app.route('/get_status/<number_id>', methods=['GET'])
def get_status(number_id):
    """Verifica status e obtém código se disponível"""
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
            code = data.split(':', 1)[1].strip()

            # Verificar se é um código NOVO
            if number_id in active_numbers:
                received_codes = active_numbers[number_id].get('received_codes', [])
                
                # Se o código já foi recebido antes, não notificar novamente
                if code in received_codes:
                    logger.info(f"ℹ️ Código {code} já foi recebido anteriormente para {number_id}")
                    result.update({
                        'has_code': False,
                        'code': None,
                        'status': 'waiting_new_code',
                        'message': 'Aguardando novo código...'
                    })
                    return jsonify(result)

            # CÓDIGO NOVO - processar normalmente
            if number_id in number_timeouts:
                number_timeouts[number_id].cancel()
                del number_timeouts[number_id]

            if number_id not in successful_numbers:
                successful_numbers.add(number_id)
                logger.info(f"✅ Primeiro código recebido para {number_id}")

            # Armazenar código NOVO
            if number_id in active_numbers:
                active_numbers[number_id]['received_codes'].append(code)
                active_numbers[number_id]['last_code'] = code
                active_numbers[number_id]['status'] = 'code_received'

            # Pedir novo SMS
            try:
                retry_url = f"{BASE_URL}?api_key={API_KEY}&action=setStatus&status=3&id={number_id}"
                retry_resp = requests.get(retry_url, timeout=5)
                logger.info(f"🔄 Novo SMS solicitado (setStatus=3): {retry_resp.text.strip()}")
            except Exception as e:
                logger.error(f"Erro ao solicitar novo SMS: {e}")

            logger.info(f"✅ NOVO código recebido para {number_id}: {code}")
            result.update({
                'has_code': True,
                'code': code,
                'status': 'received'
            })

        elif data == 'STATUS_WAIT_CODE':
            result.update({
                'message': 'Aguardando código...',
                'status': 'waiting_code'
            })
            logger.info(f"⏳ Aguardando código para {number_id}")

        elif data == 'STATUS_CANCEL' or data == 'STATUS_WAIT_RETRY':
            result.update({
                'message': 'Número expirado ou cancelado',
                'status': 'cancelled'
            })
            logger.info(f"✗ Número {number_id} expirado/cancelado")
            active_numbers.pop(number_id, None)

        else:
            result.update({
                'message': data,
                'status': 'unknown'
            })

        return jsonify(result)

    except Exception as e:
        logger.error(f"Erro em /get_status: {e}")
        return jsonify({'success': False, 'message': f'Erro: {str(e)}'}), 500


@app.route('/cancel_number/<number_id>', methods=['GET'])
def cancel_number(number_id):
    """Cancela manualmente (setStatus=8)"""
    try:
        if number_id in number_timeouts:
            number_timeouts[number_id].cancel()
            del number_timeouts[number_id]
        active_numbers.pop(number_id, None)
        successful_numbers.discard(number_id)

        url = f"{BASE_URL}?api_key={API_KEY}&action=setStatus&status=8&id={number_id}"
        response = requests.get(url, timeout=5)
        logger.info(f"✗ Número {number_id} cancelado manualmente")
        return jsonify({
            'success': True,
            'response': response.text.strip(),
            'message': 'Número cancelado'
        })
    except Exception as e:
        logger.error(f"Erro ao cancelar: {e}")
        return jsonify({'success': False, 'message': f'Erro: {str(e)}'}), 500


@app.route('/stats', methods=['GET'])
def get_stats():
    return jsonify({
        'success': True,
        'successful_numbers': len(successful_numbers),
        'active_numbers': len(active_numbers),
        'total_codes': sum(len(num.get('received_codes', [])) for num in active_numbers.values())
    })


if __name__ == '__main__':
    logger.info("🚀 Servidor SMS iniciado (HeroSMS)")
    logger.info("📞 Números brasileiros (73) - Serviço: mm")
    logger.info("⏰ Timeout: 50s")
    logger.info("⚡ Compra direta - Preço atualizado em background!")
    print("\n" + "="*50)
    app.run(debug=True, port=3000, host='0.0.0.0')
