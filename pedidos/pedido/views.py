import json
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from config.settings import API_ARTICULOS
from .models import Pedido, DetallePedido
import requests


ARTICULOS_SERVICE_URL = API_ARTICULOS['URL']
TOKEN_URL = API_ARTICULOS['TOKEN_URL']


class PedidoCreateView(APIView):
    """Vista para crear un nuevo pedido."""

    permission_classes = [IsAuthenticated]

    def post(self, request) -> JsonResponse:
        """Crea un nuevo pedido en la base de datos."""

        articulos = request.data.get('articulos', [])
        if not articulos:
            return Response({'error': 'No se proporcionaron artículos.'},
                            status=status.HTTP_400_BAD_REQUEST)

        pedido = Pedido.objects.create()
        total_sin_impuestos = 0
        total_con_impuestos = 0

        for articulo_data in articulos:
            if articulo_data['cantidad'] <= 0:
                pedido.delete()
                return Response({'error': 'La cantidad debe ser positiva.'},
                                status=status.HTTP_400_BAD_REQUEST)

            # Generar el token de autenticación
            token_response = requests.post(
                TOKEN_URL,
                data={'username': API_ARTICULOS['USERNAME'],
                      'password': API_ARTICULOS['PASSWORD']})

            # Verificación de la respuesta
            if token_response.status_code != 200:
                return Response({"error": "No se pudo obtener el token"},
                                status=token_response.status_code)

            token = token_response.json()['access']

            # Solicitud HTTP para validar y obtener la información
            headers = {'Authorization': f'Bearer {token}'}
            response = requests.get(
                f"{ARTICULOS_SERVICE_URL}{articulo_data['id']}",
                headers=headers
            )

            if response.status_code == 404:
                pedido.delete()
                return Response({'error': 'Artículo no encontrado.'},
                                status=status.HTTP_404_NOT_FOUND)

            articulo_info = response.json()
            precio_sin_impuestos = float(articulo_info['precio_sin_impuestos'])
            impuesto_aplicable = float(articulo_info['impuesto_aplicable'])
            cantidad = articulo_data['cantidad']
            total_sin_impuestos += precio_sin_impuestos * cantidad
            total_con_impuestos += (precio_sin_impuestos
                                    + (precio_sin_impuestos
                                       * impuesto_aplicable / 100)) * cantidad

            # Crear el detalle del pedido con la información obtenida
            DetallePedido.objects.create(
                pedido=pedido,
                articulo_id=articulo_info['id'],
                articulo_referencia=articulo_info['referencia'],
                articulo_nombre=articulo_info['nombre'],
                articulo_precio_sin_impuestos=precio_sin_impuestos,
                articulo_impuesto_aplicable=impuesto_aplicable,
                cantidad=cantidad
            )

        pedido.precio_total_sin_impuestos = total_sin_impuestos
        pedido.precio_total_con_impuestos = total_con_impuestos
        pedido.save()

        return Response({'id': pedido.id}, status=status.HTTP_201_CREATED)


class PedidoEditView(APIView):
    """Vista para editar un pedido existente."""

    permission_classes = [IsAuthenticated]

    def put(self, request, id) -> JsonResponse:
        """Edita un pedido existente."""

        data = json.loads(request.body)
        articulos_data = data.get('articulos', [])

        pedido = get_object_or_404(Pedido, id=id)

        if not articulos_data:
            return JsonResponse({'error': 'Debe incluir al menos un artículo'},
                                status=400)

        # Limpiar los artículos anteriores
        pedido.detallepedido_set.all().delete()

        for articulo_data in articulos_data:
            articulo_id = articulo_data['id']
            cantidad = articulo_data['cantidad']

            if cantidad <= 0:
                return JsonResponse({
                    'error':
                    f"La cantidad de {articulo_id} debe ser mayor que 0"},
                    status=400)

            # Generar el token de autenticación
            token_response = requests.post(
                TOKEN_URL,
                data={'username': API_ARTICULOS['USERNAME'],
                      'password': API_ARTICULOS['PASSWORD']})
            # Verificación de la respuesta
            if token_response.status_code != 200:
                return Response({"error": "No se pudo obtener el token"},
                                status=token_response.status_code)

            token = token_response.json()['access']

            # Solicitud HTTP para validar y obtener la información
            headers = {'Authorization': f'Bearer {token}'}
            articulo_response = requests.get(
                f"{ARTICULOS_SERVICE_URL}{articulo_id}", headers=headers)

            if articulo_response.status_code != 200:
                return JsonResponse(
                    {'error':
                     f"Artículo con referencia {articulo_id} no encontrado"},
                    status=404)

            articulo_info = articulo_response.json()

            # Actualizar el artículo en el microservicio de Artículos
            update_data = {
                'referencia': articulo_info['referencia'],
                'nombre': articulo_info['nombre'],
                'descripcion': articulo_info['descripcion'],
                'precio_sin_impuestos': articulo_info['precio_sin_impuestos'],
                'impuesto_aplicable': articulo_info['impuesto_aplicable']
            }

            articulo_update_response = requests.put(
                f"{ARTICULOS_SERVICE_URL}{articulo_id}",
                json=update_data,
                headers=headers)

            if articulo_update_response.status_code != 200:
                return JsonResponse({
                    'error':
                    f"Error al actualizar el artículo {articulo_id}"},
                    status=articulo_update_response.status_code)

            DetallePedido.objects.create(
                pedido=pedido,
                articulo_id=articulo_id,
                articulo_referencia=articulo_info['referencia'],
                articulo_nombre=articulo_info['nombre'],
                articulo_precio_sin_impuestos=articulo_info[
                    'precio_sin_impuestos'],
                articulo_impuesto_aplicable=articulo_info[
                    'impuesto_aplicable'],
                cantidad=cantidad
            )

        pedido.calcular_precio_total()

        return JsonResponse({
            'id': pedido.id,
            'articulos': [{
                'articulo_id': detalle.articulo_id,
                'referencia': detalle.articulo_referencia,
                'nombre': detalle.articulo_nombre,
                'cantidad': detalle.cantidad,
                'precio_sin_impuestos': detalle.articulo_precio_sin_impuestos,
                'precio_con_impuestos': detalle.articulo_precio_sin_impuestos
                * (1 + detalle.articulo_impuesto_aplicable / 100)
            } for detalle in pedido.detallepedido_set.all()],
            'precio_total_sin_impuestos': pedido.precio_total_sin_impuestos,
            'precio_total_con_impuestos': pedido.precio_total_con_impuestos,
            'fecha_creacion': pedido.fecha_creacion
        }, status=200)


class PedidoDetailView(APIView):
    """Vista para obtener un pedido por su ID."""

    permission_classes = [IsAuthenticated]

    def get(self, request, id) -> JsonResponse:
        """Obtiene el detalle de un pedido."""

        pedido = get_object_or_404(Pedido, id=id)
        detalles = pedido.detallepedido_set.all()

        return JsonResponse({
            'id': pedido.id,
            'articulos': [{
                'referencia': detalle.articulo_referencia,
                'nombre': detalle.articulo_nombre,
                'cantidad': detalle.cantidad,
                'precio_sin_impuestos': detalle.articulo_precio_sin_impuestos,
                'precio_con_impuestos': detalle.articulo_precio_sin_impuestos
                * (1 + detalle.articulo_impuesto_aplicable / 100)
            } for detalle in detalles],
            'precio_total_sin_impuestos': pedido.precio_total_sin_impuestos,
            'precio_total_con_impuestos': pedido.precio_total_con_impuestos,
            'fecha_creacion': pedido.fecha_creacion
        })


class PedidoListView(APIView):
    """Vista para obtener todos los pedidos."""

    permission_classes = [IsAuthenticated]

    def get(self, request) -> JsonResponse:
        """Obtiene todos los pedidos."""

        pedidos = Pedido.objects.all()

        return JsonResponse([{
            'id': pedido.id,
            'articulos': [{
                'referencia': detalle.articulo_referencia,
                'nombre': detalle.articulo_nombre,
                'cantidad': detalle.cantidad,
                'precio_sin_impuestos': detalle.articulo_precio_sin_impuestos,
                'precio_con_impuestos': detalle.articulo_precio_sin_impuestos
                * (1 + detalle.articulo_impuesto_aplicable / 100)
            } for detalle in pedido.detallepedido_set.all()],
            'precio_total_sin_impuestos': pedido.precio_total_sin_impuestos,
            'precio_total_con_impuestos': pedido.precio_total_con_impuestos,
            'fecha_creacion': pedido.fecha_creacion
        } for pedido in pedidos], safe=False)
