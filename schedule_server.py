from flask import Flask, jsonify, request
from schedule_db import get_all_programs, add_program, update_program, delete_program

app = Flask(__name__)

@app.route('/', methods=['GET'])
def test():
    return "Schedule server running on 5005"

@app.route('/next-show', methods=['GET'])
def api_get_programs():
    return jsonify(get_all_programs())

@app.route('/next-show', methods=['POST'])
def api_add_program():
    try:
        data = request.json
        new_id = add_program(data)  # без фото пока
        return jsonify({'id': new_id, 'status': 'created'}), 201
    except Exception as e:
        return jsonify({'error': str(e)}), 400

@app.route('/next-show/<int:id>', methods=['PUT'])
def api_update_program(id):
    try:
        data = request.json
        update_program(id, data)
        return jsonify({'status': 'updated'})
    except Exception as e:
        return jsonify({'error': str(e)}), 400

@app.route('/next-show/<int:id>', methods=['DELETE'])
def api_delete_program(id):
    try:
        delete_program(id)
        return jsonify({'status': 'deleted'})
    except Exception as e:
        return jsonify({'error': str(e)}), 400

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5005, debug=True)
