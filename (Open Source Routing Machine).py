import streamlit as st
import pandas as pd
import numpy as np
import folium
from streamlit_folium import st_folium
import math
import requests
import io
import time

# Config Halaman Utama
st.set_page_config(page_title="Smart Routing OSRM Optimizer", layout="wide")
st.title("🚚 Smart Routing & Vehicle Load Optimizer (Final OSRM Version)")
st.write("Aplikasi pengelompokan pengiriman berdasarkan Rit, Zona, Urutan Picking, Kapasitas Kubikasi, dan Rute Jalan Nyata OSRM.")

# ==============================================================================
# 1. PARAMETER CONFIGURATION (SIDEBAR)
# ==============================================================================
st.sidebar.header("📍 Titik Koordinat DC")
dc_lat = st.sidebar.number_input("Latitude DC", value=-6.209462, format="%.6f")
dc_lon = st.sidebar.number_input("Longitude DC", value=106.629741, format="%.6f")
dc_coord = (dc_lat, dc_lon)

st.sidebar.header("⚙️ Batasan & Kriteria (Constraints)")
max_picking_diff = st.sidebar.number_input("Maksimal Selisih Urutan Picking", value=15, min_value=1)
min_shops = st.sidebar.number_input("Minimal Toko per Paket", value=2, min_value=1)
max_shops = st.sidebar.number_input("Maksimal Toko per Paket", value=4, min_value=1)
vehicle_speed = st.sidebar.number_input("Kecepatan Kendaraan (km/jam)", value=40, min_value=1)
unloading_time = st.sidebar.number_input("Durasi Unloading per Toko (Jam)", value=0.5, min_value=0.0, step=0.1)

st.sidebar.header("🚛 Manajemen Armada & Unit")

if 'vehicles' not in st.session_state:
    st.session_state.vehicles = [
        {"tipe": "CDE", "kapasitas": 9.0, "jumlah": 10},
        {"tipe": "CDD", "kapasitas": 14.0, "jumlah": 5},
        {"tipe": "L300", "kapasitas": 4.0, "jumlah": 15},
        {"tipe": "Minibus", "kapasitas": 2.0, "jumlah": 5}
    ]

col_plus, col_minus = st.sidebar.columns(2)
if col_plus.button("➕ Tambah Tipe"):
    st.session_state.vehicles.append({"tipe": f"Tipe_{len(st.session_state.vehicles)+1}", "kapasitas": 5.0, "jumlah": 5})
if col_minus.button("➖ Hapus Tipe") and len(st.session_state.vehicles) > 1:
    st.session_state.vehicles.pop()

updated_vehicles = []
for i, v in enumerate(st.session_state.vehicles):
    with st.sidebar.expander(f"Unit: {v['tipe']}", expanded=True):
        t = st.text_input(f"Nama Tipe", value=v['tipe'], key=f"t_{i}")
        k = st.number_input(f"Kapasitas (m³)", value=v['kapasitas'], min_value=0.1, key=f"k_{i}")
        j = st.number_input(f"Jumlah Unit Ready", value=v['jumlah'], min_value=0, key=f"j_{i}")
        updated_vehicles.append({"tipe": t, "kapasitas": k, "jumlah": j})
st.session_state.vehicles = updated_vehicles

# ==============================================================================
# 2. LOGIKA PERHITUNGAN RUTE & OSRM API
# ==============================================================================

def haversine_distance(coord1, coord2):
    try:
        lat1, lon1 = coord1
        lat2, lon2 = coord2
        R = 6371.0 
        dlat = math.radians(lat2 - lat1)
        dlon = math.radians(lon2 - lon1)
        a = math.sin(dlat / 2) ** 2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2
        c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
        return R * c
    except:
        return 999.0

def get_osrm_route(coords):
    """ Mengambil rute jalan asli dari Open Source Routing Machine API """
    valid_coords = []
    for c in coords:
        try:
            lat = float(str(c[0]).replace(',', '.'))
            lon = float(str(c[1]).replace(',', '.'))
            valid_coords.append((lat, lon))
        except:
            continue
            
    if len(valid_coords) < 2:
        return 0.0, 0.0, []

    # Format OSRM: lon,lat;lon,lat
    coord_string = ";".join([f"{lon},{lat}" for lat, lon in valid_coords])
    url = f"http://router.project-osrm.org/route/v1/driving/{coord_string}?overview=full&geometries=geojson"
    
    try:
        response = requests.get(url, timeout=4)
        if response.status_code == 200:
            data = response.json()
            if data['code'] == 'Ok':
                route = data['routes'][0]
                distance_km = route['distance'] / 1000.0
                duration_hours = route['duration'] / 3600.0
                geometry = [[pt[1], pt[0]] for pt in route['geometry']['coordinates']]
                return distance_km, duration_hours, geometry
    except:
        pass
    
    # KONDISI CADANGAN (Jika API OSRM penuh/down agar program tidak eror)
    total_dist = 0.0
    for i in range(len(valid_coords) - 1):
        total_dist += haversine_distance(valid_coords[i], valid_coords[i+1])
    # Kompensasi rute darat berbelok di Indonesia (Jarak Udara x 1.3)
    estimated_road_dist = total_dist * 1.3
    return estimated_road_dist, (estimated_road_dist / vehicle_speed), valid_coords

def make_gmaps_link(dc, stores):
    link = f"https://www.google.com/maps/dir/{dc[0]},{dc[1]}/"
    for s in stores:
        link += f"{s[0]},{s[1]}/"
    link += f"{dc[0]},{dc[1]}"
    return link

# ==============================================================================
# 3. CORE PROCESSING DATA
# ==============================================================================
uploaded_file = st.file_uploader("Upload File CSV Data Toko Anda (Separator ';')", type=["csv"])

if uploaded_file is not None:
    try:
        df = pd.read_csv(uploaded_file, sep=';')
        
        # Penyelamat Error Kolom
        required_cols = ['KD TOKO', 'NO PICK', 'Rit', 'ZONA', 'TOTAL', 'Latitude', 'Longitude']
        missing = [c for c in required_cols if c not in df.columns]
        if missing:
            st.error(f"Kolom CSV tidak sesuai. Kolom hilang: {missing}")
            st.stop()
            
        # Data Cleaning & Handling Koordinat Kosong '-'
        df['TOTAL'] = df['TOTAL'].astype(str).str.replace(',', '.').astype(float)
        df['NO PICK'] = pd.to_numeric(df['NO PICK'], errors='coerce').fillna(0).astype(int)
        df['Rit'] = pd.to_numeric(df['Rit'], errors='coerce').fillna(1).astype(int)
        
        # Pisahkan toko yang tidak punya koordinat valid agar diproses di bagian terpisah
        valid_geo = (df['Latitude'].astype(str) != '-') & (df['Longitude'].astype(str) != '-')
        df_valid = df[valid_geo].copy()
        df_invalid = df Brenner = df[~valid_geo].copy() if hasattr(df, 'Brenner') else df[~valid_geo].copy()

        # LOGIKA 1 & 2: Urutkan Rit secara Descending (Rit 2 dikerjakan DULUAN, baru Rit 1)
        df_valid = df_valid.sort_values(by=['Rit', 'ZONA', 'NO PICK'], ascending=[False, True, True]).reset_index(drop=True)
        
        # Sorting kapasitas tipe truk dari terbesar
        sorted_trucks = sorted(st.session_state.vehicles, key=lambda x: x['kapasitas'], reverse=True)
        
        unassigned = df_valid.to_dict('records')
        trips = []
        trip_id = 1
        
        st.info(f"Memproses {len(df_valid)} toko berkoordinat valid dan {len(df_invalid)} toko tanpa koordinat.")
        progress_bar = st.progress(0)
        
        # PROSES CLUSTERING & ROUTING
        while len(unassigned) > 0:
            current_store = unassigned.pop(0)
            
            # Pilih Armada
            chosen_vehicle = None
            for truck in sorted_trucks:
                if truck['jumlah'] > 0:
                    chosen_vehicle = truck
                    break
            if not chosen_vehicle:
                chosen_vehicle = sorted_trucks[0] # Default cadangan jika kuota truk habis
                
            current_trip_stores = [current_store]
            stores_to_remove = []
            
            # Gabungkan dengan toko pasangan terdekat
            for potential in unassigned:
                if len(current_trip_stores) >= max_shops:
                    break
                if potential['Rit'] != current_store['Rit']:
                    continue
                if potential['ZONA'] != current_store['ZONA']:
                    continue
                    
                # Cek Batasan Kubikasi m3
                total_m3 = sum(s['TOTAL'] for s in current_trip_stores) + potential['TOTAL']
                if total_m3 > chosen_vehicle['kapasitas']:
                    potential['FAIL_REASON'] = "Kubikasi mobil tidak muat"
                    continue
                    
                # Cek Batasan Urutan Picking
                all_picks = [s['NO PICK'] for s in current_trip_stores] + [potential['NO PICK']]
                if (max(all_picks) - min(all_picks)) > max_picking_diff:
                    potential['FAIL_REASON'] = "Urut picking toko pasangan terlalu jauh"
                    continue
                    
                current_trip_stores.append(potential)
                stores_to_remove.append(potential)
                
            for s in stores_to_remove:
                unassigned.remove(s)
                
            # Kurangi inventory truk aktual
            if chosen_vehicle['jumlah'] > 0:
                for v in st.session_state.vehicles:
                    if v['tipe'] == chosen_vehicle['tipe']:
                        v['jumlah'] -= 1
                        break
            
            # Tentukan Status Pengelompokan & Alasan Solusi Toko Tunggal
            status_pasangan = "Sukses Berpasangan"
            if len(current_trip_stores) < min_shops:
                status_pasangan = f"Toko Tunggal. Alasan: {current_store.get('FAIL_REASON', 'Zona area hanya terisi 1 toko')}. Otomatis dicarikan rute langsung via Google Maps."
            
            # Build Koordinat Perjalanan
            route_points = [dc_coord]
            store_points_only = []
            for s in current_trip_stores:
                lat = float(str(s['Latitude']).replace(',', '.'))
                lon = float(str(s['Longitude']).replace(',', '.'))
                route_points.append((lat, lon))
                store_points_only.append((lat, lon))
            route_points.append(dc_coord)
            
            # HITUNG JARAK ASLI MENGGUNAKAN OSRM
            distance_km, drive_time_hours, geo_lines = get_osrm_route(route_points)
            total_time = drive_time_hours + (len(current_trip_stores) * unloading_time)
            
            gmaps_url = make_gmaps_link(dc_coord, store_points_only)
            
            trips.append({
                "ID_TRIP": f"TRIP-{trip_id:03d}",
                "TIPE_ARMADA": chosen_vehicle['tipe'],
                "RIT": current_store['Rit'],
                "ZONA": current_store['ZONA'],
                "JUMLAH_TOKO": len(current_trip_stores),
                "DAFTAR_TOKO": ", ".join([s['KD TOKO'] for s in current_trip_stores]),
                "TOTAL_M3": sum(s['TOTAL'] for s in current_trip_stores),
                "JARAK_OSRM_KM": round(distance_km, 2),
                "DURASI_TOTAL_JAM": round(total_time, 2),
                "STATUS_LOGISTIK": status_pasangan,
                "GOOGLE_MAPS_LINK": gmaps_url,
                "GEO_LINES": geo_lines,
                "STORE_LIST_RAW": current_trip_stores
            })
            trip_id += 1
            progress_bar.progress(min(1.0, trip_id / (len(df_valid)/2 + 1)))
            
        # Penanganan Toko Tanpa Koordinat Valid (Jika Ada)
        for idx, row in df_invalid.iterrows():
            trips.append({
                "ID_TRIP": f"TRIP-ERR-{trip_id:03d}",
                "TIPE_ARMADA": "Belum Ditentukan",
                "RIT": row['Rit'],
                "ZONA": row['ZONA'],
                "JUMLAH_TOKO": 1,
                "DAFTAR_TOKO": row['KD TOKO'],
                "TOTAL_M3": row['TOTAL'],
                "JARAK_OSRM_KM": 0.0,
                "DURASI_TOTAL_JAM": 0.0,
                "STATUS_LOGISTIK": "GAGAL PROSES: Koordinat toko di CSV bernilai '-'",
                "GOOGLE_MAPS_LINK": "",
                "GEO_LINES": [],
                "STORE_LIST_RAW": [row]
            })
            trip_id += 1
            
        progress_bar.progress(1.0)
        df_output = pd.DataFrame(trips)
        
        # ==============================================================================
        # 4. OUTPUT DISPLAY (INTERFACE)
        # ==============================================================================
        st.header("📋 Hasil Akhir Optimasi Pengiriman")
        
        c1, c2, c3 = st.columns(3)
        c1.metric("Total Truk Jalan", len(df_output[df_output['JARAK_OSRM_KM'] > 0]))
        c2.metric("Total Kubikasi", f"{df_output['TOTAL_M3'].sum():.2f} m³")
        c3.metric("Total Jarak Jalan (OSRM)", f"{df_output['JARAK_OSRM_KM'].sum():.1f} KM")
        
        cols_display = ["ID_TRIP", "TIPE_ARMADA", "RIT", "ZONA", "JUMLAH_TOKO", "DAFTAR_TOKO", "TOTAL_M3", "JARAK_OSRM_KM", "DURASI_TOTAL_JAM", "STATUS_LOGISTIK", "GOOGLE_MAPS_LINK"]
        st.dataframe(df_output[cols_display], use_container_width=True)
        
        # Tombol Download Excel
        buffer = io.BytesIO()
        with pd.ExcelWriter(buffer, engine='xlsxwriter') as writer:
            df_output[cols_display].to_excel(writer, index=False, sheet_name='Summary_OSRM')
        st.download_button(
            label="📥 Download Hasil Rute ke Excel",
            data=buffer.getvalue(),
            file_name="Rute_Pengiriman_OSRM.xlsx",
            mime="application/vnd.ms-excel"
        )
        
        # PETA VISUALISASI OSRM
        st.write("---")
        st.header("🗺️ Tampilan Rute Peta Jalan Raya OSRM")
        
        selectable_trips = df_output[df_output['JARAK_OSRM_KM'] > 0]['ID_TRIP'].tolist()
        selected_id = st.selectbox("Pilih ID Trip untuk melacak rute jalan:", selectable_trips)
        
        selected_row = df_output[df_output['ID_TRIP'] == selected_id].iloc[0]
        
        m_col, d_col = st.columns([2, 1])
        
        with d_col:
            st.subheader(f"Detail Jalur {selected_id}")
            st.markdown(f"**Truk Terpilih:** {selected_row['TIPE_ARMADA']}")
            st.markdown(f"**Jarak Tempuh:** {selected_row['JARAK_OSRM_KM']} KM")
            st.markdown(f"🔗 [**Buka di Google Maps Handphone**]({selected_row['GOOGLE_MAPS_LINK']})")
            
            st.write("**Urutan Bongkar Muatan Toko:**")
            st.write("1. 🏢 **START: DISTRIBUTION CENTER (DC)**")
            for index, s_data in enumerate(selected_row['STORE_LIST_RAW'], start=2):
                st.write(f"{index}. 🏪 **{s_data['KD TOKO']}** (No Pick: {s_data['NO PICK']} | Vol: {s_data['TOTAL']} m³)")
            st.write(f"{len(selected_row['STORE_LIST_RAW'])+2}. 🏢 **FINISH: KEMBALI KE DC**")
            
        with m_col:
            # Generate Peta Folium
            mymap = folium.Map(location=[dc_coord[0], dc_coord[1]], zoom_start=12)
            
            # Marker DC
            folium.Marker(dc_coord, popup="TITIK DC UTAMA", icon=folium.Icon(color="red", icon="briefcase")).add_to(mymap)
            
            # Marker Toko-Toko dalam Trip
            for s_data in selected_row['STORE_LIST_RAW']:
                try:
                    lat_t = float(str(s_data['Latitude']).replace(',', '.'))
                    lon_t = float(str(s_data['Longitude']).replace(',', '.'))
                    folium.Marker(
                        [lat_t, lon_t],
                        popup=f"Toko: {s_data['KD TOKO']}<br>Vol: {s_data['TOTAL']} m³",
                        icon=folium.Icon(color="blue", icon="shopping-cart")
                    ).add_to(mymap)
                except:
                    pass
            
            # Gambar Polyline asli OSRM (Belokan Jalan)
            if selected_row['GEO_LINES']:
                folium.PolyLine(selected_row['GEO_LINES'], color="blue", weight=5, opacity=0.8).add_to(mymap)
                
            st_folium(mymap, width=720, height=450, returned_objects=[])

    except Exception as e:
        st.error(f"Terjadi kesalahan pemrosesan file atau parameter. Pastikan format kolom CSV Anda sesuai. Detail: {str(e)}")
else:
    st.info("Silakan unggah file CSV logistik Anda untuk memulai perhitungan.")