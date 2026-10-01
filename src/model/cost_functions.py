from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from model.vehicle import VehicleRegistry


def free(duration_s, distance_m):
    return 0.0, 0.0


def owned_car_cost(duration_s, distance_m):
    distance_m = distance_m or 0
    distance_km = distance_m / 1000.0
    avg_kml = 15.305 
    avg_eurl = 2.129
    return 0.0, (distance_km / avg_kml) * avg_eurl

def taxi_cost(duration_s, distance_m):
    distance_m = distance_m or 0
    distance_km = distance_m / 1000.0
    duration_s = duration_s or 0
    duration_m = duration_s / 60

    by_km = 3.90 + 1.50 * distance_km
    by_min = 3.90 + 0.50 * duration_m
    if by_km > by_min:
        return 0.0, by_km
    else: return 0.0, by_min

def public_transport_cost(duration_s, distance_m):
    distance_m = distance_m or 0
    distance_km = distance_m / 1000.0
    duration_s = duration_s or 0
    duration_m = duration_s / 60
    if distance_km <= 2.0 and duration_m <= 10:
        return 0.0, 2.80
    else:
        return 0.0, 4.0


def public_transport_daily_cost(duration_s, distance_m):
    return 11.20, 0.0


def public_transport_monthly_cost(duration_s, distance_m):
    return 63.0, 0.0

def discount_public_transport_monthly_cost(duration_s, distance_m):
    return 9.0, 0.0

def daily_rental_car_cost(duration_s, distance_m):
    return 40.0, 0.0


def long_term_rental_bike_cost(duration_s, distance_m):
    return 14.90, 0.0

def short_term_rental_bike_cost(duration_s, distance_m):
    duration_s = duration_s or 0
    duration_minutes = duration_s / 60.0
    return 0.0, 1.0 + 0.10 * duration_minutes

def air_taxi_cost(duration_s, distance_m):
    return 0.0, 5.0


COST_FUNCTIONS = {
    "free": free,
    "owned_car_cost": owned_car_cost,
    "public_transport_cost": public_transport_cost,
    "public_transport_daily_cost": public_transport_daily_cost,
    "public_transport_monthly_cost": public_transport_monthly_cost,
    "taxi_cost": taxi_cost,
    "daily_rental_car_cost": daily_rental_car_cost,
    "long_term_rental_bike_cost": long_term_rental_bike_cost,
    "short_term_rental_bike_cost": short_term_rental_bike_cost,
    "discount_public_transport_monthly_cost": discount_public_transport_monthly_cost,
    "air_taxi_cost": air_taxi_cost,
}


def get_cost_function(name):
    try:
        return COST_FUNCTIONS[name]
    except KeyError as exc:
        raise ValueError(f"Unknown cost function: {name}") from exc


def get_vehicle_activation_cost(vehicle) -> float:
    activation_cost, _ = vehicle.cost_function()(0, 0)
    return activation_cost or 0.0


def get_activation_cost_vehicles(vehicle_registry: "VehicleRegistry") -> list:
    activation_cost_vehicles = []
    for vehicle in vehicle_registry.vehicles:
        try:
            if get_vehicle_activation_cost(vehicle) > 0:
                activation_cost_vehicles.append(vehicle)
        except Exception:
            continue
    return activation_cost_vehicles
