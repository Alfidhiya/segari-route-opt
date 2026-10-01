from datetime import datetime, time, timedelta
import itertools
import pandas as pd
import streamlit as st

st.set_page_config(
    page_title="Segari B2B Fleet Route Optimization",
    layout="wide",
)


# ==========================================
# 1. HELPER WAKTU
# ==========================================
def time_to_minutes(t_val):
    if isinstance(t_val, time):
        return t_val.hour * 60 + t_val.minute
    elif isinstance(t_val, str):
        try:
            parts = t_val.strip().split(":")
            return int(parts[0]) * 60 + int(parts[1])
        except Exception:
            return 8 * 60
    return 0


def get_driver_start_minutes(t_val):
    """
    Jika jam masuk driver antara 18.00 - 23.59, maka dianggap Hari H-1 
    (menit negatif relatif terhadap Jam 00:00 Hari H).
    """
    mins = time_to_minutes(t_val)
    if mins >= 18 * 60:  # 18:00 ke atas
        return mins - 1440  # H-1
    return mins


def minutes_to_hhmm(mins):
    """Format menit relatif ke penanda waktu HH:MM (H-1, Hari H, atau H+1)."""
    if mins < 0:
        mins_pos = mins + 1440
        h = (int(mins_pos) % (24 * 60)) // 60
        m = int(mins_pos) % 60
        return f"{h:02d}:{m:02d} (H-1)"
    
    h = (int(mins) % (24 * 60)) // 60
    m = int(mins) % 60
    time_str = f"{h:02d}:{m:02d}"
    if mins >= 1440:
        return f"{time_str} (H+1)"
    return time_str


# ==========================================
# 2. LOAD DATASET & ROUTE MATRIX
# ==========================================
@st.cache_data
def load_and_prep_data():
    try:
        df_rute_awal = pd.read_csv("master_rute_awal.csv")
        df_b2b = pd.read_csv("master_B2B.csv")
        df_cost = pd.read_csv("master_cost.csv")
        df_matrix = pd.read_csv("route_matrix.csv")

        # Bersihkan format angka di master cost jika berupa string
        cols_to_clean = [
            "harga_bbm_per_liter", "sewa_armada_per_jam", 
            "driver_cost_per_jam", "rasio_bbm"
        ]
        for col in cols_to_clean:
            if col in df_cost.columns and df_cost[col].dtype == "object":
                df_cost[col] = df_cost[col].astype(str).str.replace(",", "").str.replace(".", "").astype(float)

        # Pastikan format ID berupa string tanpa spasi berlebih
        df_matrix["start_wh_document_id"] = df_matrix["start_wh_document_id"].astype(str).str.strip()
        df_matrix["end_wh_document_id"] = df_matrix["end_wh_document_id"].astype(str).str.strip()

        # Buat dictionary lookup cepat: (start_id, end_id) -> (jarak_km, waktu_tempuh_menit)
        route_matrix_dict = {
            (row["start_wh_document_id"], row["end_wh_document_id"]): (
                float(row["jarak_km"]),
                float(row["waktu_tempuh_menit"])
            )
            for _, row in df_matrix.iterrows()
        }

        # Kumpulan ID Toko & B2B
        store_ids = set(df_rute_awal[df_rute_awal["tipe_wh"] == "SDD_WAREHOUSE"]["wh_document_id"].astype(str).str.strip())
        b2b_ids = set(df_b2b["wh_document_id"].astype(str).str.strip())

        return df_rute_awal, df_b2b, df_cost, route_matrix_dict, store_ids, b2b_ids
    except Exception as e:
        st.error(f"Gagal memuat file CSV: {e}")
        return None, None, None, None, None, None


df_rute_awal, df_b2b, df_cost, route_matrix_dict, store_ids, b2b_ids = load_and_prep_data()

MASTER_ROUTES = {
    "DMT-DMG-BKS": ["DMT", "DMG", "BKS"],
    "DMT-DMG-CBR": ["DMT", "DMG", "CBR"],
    "DMT-DMG-KJT": ["DMT", "DMG", "KJT"],
    "DMT-MBS": ["DMT", "MBS"],
    "DMT-BSD": ["DMT", "BSD"],
    "DMT-GDS": ["DMT", "GDS"],
    "DMT-KL_KJ": ["DMT", "KL_KJ"],
    "DMT-RWG": ["DMT", "RWG"],
    "DMT-STB": ["DMT", "STB"],
    "DMT-DMG-CRD": ["DMT", "DMG", "CRD"],
    "DMT-DMG-MPG": ["DMT", "DMG", "MPG"],
    "DMT-KL_CKG": ["DMT", "KL_CKG"],
}


# ==========================================
# 3. HELPER ROUTING ENGINE & COST LOGIC
# ==========================================
def get_route_details(loc_ids, matrix_dict):
    """
    Menghitung total jarak, total waktu tempuh, dan daftar durasi tiap kaki rute (leg)
    berdasarkan data dari route_matrix.csv.
    """
    if not loc_ids or len(loc_ids) < 2:
        return None, None, None

    dist_km = 0.0
    dur_min = 0.0
    leg_durs = []

    for i in range(len(loc_ids) - 1):
        start = str(loc_ids[i]).strip()
        end = str(loc_ids[i + 1]).strip()

        if start == end:
            leg_dist, leg_dur = 0.0, 0.0
        elif (start, end) in matrix_dict:
            leg_dist, leg_dur = matrix_dict[(start, end)]
        else:
            # Jika pasangan titik tidak ditemukan pada route_matrix
            return None, None, None

        dist_km += leg_dist
        dur_min += leg_dur
        leg_durs.append(leg_dur)

    return dist_km, dur_min, leg_durs


def calculate_node_loading(node, b2b_loading_dict):
    if node == "DMG":
        return 30
    elif node in store_ids:
        return 120
    elif node in b2b_loading_dict:
        return int(b2b_loading_dict[node])
    return 0


def calculate_cost(distance_km, duration_min, cost_config):
    duration_hour = duration_min / 60.0
    b_bbm = (distance_km / float(cost_config["rasio_bbm"])) * float(cost_config["harga_bbm_per_liter"])
    b_driver = duration_hour * float(cost_config["driver_cost_per_jam"])
    b_sewa = duration_hour * float(cost_config["sewa_armada_per_jam"])
    return round(b_bbm), round(b_driver), round(b_sewa), round(b_bbm + b_driver + b_sewa)


def evaluate_route_timeline(full_path, leg_durs, b2b_loading_dict, b2b_open_dict, dep_time_min, driver_start_min):
    curr_min = dep_time_min
    
    start_node = full_path[0]
    start_load = calculate_node_loading(start_node, b2b_loading_dict)
    total_loading_min = start_load
    total_waiting_min = 0
    
    combined_travel_unload = []
    loading_details = [f"{start_node}: {start_load} Mnt"] if start_load > 0 else []
    waiting_details = []
    b2b_arrivals = []

    for i in range(1, len(full_path)):
        from_node = full_path[i - 1]
        to_node = full_path[i]
        leg_dur = leg_durs[i - 1]
        
        curr_min += leg_dur
        arrival_time_str = minutes_to_hhmm(curr_min)
        
        if to_node in b2b_ids:
            b2b_arrivals.append(f"{to_node} (Tiba {arrival_time_str})")
            
            # Pengecekan Jam Buka B2B
            open_time_min = time_to_minutes(b2b_open_dict.get(to_node, time(8, 0)))
            if curr_min < open_time_min:
                wait_dur = open_time_min - curr_min
                waiting_details.append(f"{to_node}: {round(wait_dur, 1)} Mnt (Tunggu buka {minutes_to_hhmm(open_time_min)})")
                total_waiting_min += wait_dur
                curr_min = open_time_min  # Menunggu hingga B2B Buka
        
        n_load = calculate_node_loading(to_node, b2b_loading_dict)
        total_loading_min += n_load
        if n_load > 0:
            loading_details.append(f"{to_node}: {n_load} Mnt")
        
        # Gabungan Durasi Perjalanan + Unloading per titik stop
        combined_dur = leg_dur + n_load
        combined_travel_unload.append(f"{from_node} -> {to_node}: {round(combined_dur, 1)} Mnt")

        curr_min += n_load

    finish_time_min = curr_min
    driver_work_duration = finish_time_min - driver_start_min
    is_overtime = driver_work_duration > (9 * 60)

    details_dict = {
        "combined_str": " | ".join(combined_travel_unload),
        "loading_str": " | ".join(loading_details),
        "waiting_str": " | ".join(waiting_details) if waiting_details else "Tidak Ada",
        "b2b_arrivals_str": " | ".join(b2b_arrivals) if b2b_arrivals else "-",
        "dmt_finish_str": minutes_to_hhmm(finish_time_min),
    }

    return finish_time_min, total_loading_min, total_waiting_min, is_overtime, driver_work_duration, details_dict


# ==========================================
# 4. INTERFACE STREAMLIT
# ==========================================
st.title("Segari B2B Fleet Route Optimization")

if df_cost is not None and route_matrix_dict is not None:
    st.sidebar.header("1. Pengaturan Armada & Waktu")
    jenis_armada = st.sidebar.selectbox("Pilih Jenis Armada", df_cost["tipe_kendaraan"].unique())
    cost_setting = df_cost[df_cost["tipe_kendaraan"] == jenis_armada].iloc[0]

    col_t1, col_t2 = st.sidebar.columns(2)
    with col_t1:
        jam_masuk_driver = st.sidebar.time_input("Jam Masuk Driver", value=time(23, 0))
    with col_t2:
        jam_berangkat_armada = st.sidebar.time_input("Jam Berangkat (Hari H)", value=time(0, 30))

    st.sidebar.markdown("---")
    st.sidebar.header("2. Aturan Kapasitas B2B")
    mode_kapasitas = st.sidebar.radio(
        "Mode Kapasitas", 
        ["Otomatis (Berdasarkan Jumlah Input)", "Manual (Maksimal 2)", "Manual (Maksimal 3)", "Manual (Maksimal 4)"]
    )

    st.subheader("Pilih Titik B2B yang Akan Dikirim")
    available_b2bs = sorted(list(b2b_ids))
    input_b2b_list = st.multiselect("Daftar ID B2B Tujuan:", options=available_b2bs, default=[])

    b2b_loading_dict = {}
    b2b_open_dict = {}

    if input_b2b_list:
        st.markdown("---")
        st.markdown("#### Input Parameter Spesifik per Titik B2B")
        
        # Judul Kolom Parameter
        h1, h2, h3 = st.columns([1, 2, 2])
        with h1:
            st.markdown("**Titik B2B**")
        with h2:
            st.markdown("**Durasi Unloading B2B**")
        with h3:
            st.markdown("**Jam Tiba Maksimal B2B**")

        for b2b_id in input_b2b_list:
            c1, c2, c3 = st.columns([1, 2, 2])
            with c1:
                st.write("") 
                st.markdown(f"**{b2b_id}**")
            with c2:
                b2b_loading_dict[b2b_id] = st.number_input(
                    f"Loading (Menit) - {b2b_id}", 
                    min_value=5, max_value=240, value=35, step=5,
                    label_visibility="collapsed"
                )
            with c3:
                b2b_open_dict[b2b_id] = st.time_input(
                    f"Jam Buka B2B - {b2b_id}", 
                    value=time(8, 0),
                    label_visibility="collapsed"
                )

    st.markdown("---")

    total_input = len(input_b2b_list)
    if "2" in mode_kapasitas: max_b2b_per_trip = 2
    elif "3" in mode_kapasitas: max_b2b_per_trip = 3
    elif "4" in mode_kapasitas: max_b2b_per_trip = 4
    else: max_b2b_per_trip = 2 if total_input <= 6 else (3 if total_input <= 12 else 4)

    btn_process = st.button("Ensemble & Hitung Alokasi Rute", type="primary")

    if btn_process:
        if not input_b2b_list:
            st.warning("Silakan pilih minimal 1 B2B tujuan.")
        else:
            with st.spinner("Mengkalkulasi alokasi rute teroptimal..."):
                driver_start_min = get_driver_start_minutes(jam_masuk_driver)
                dep_time_min = time_to_minutes(jam_berangkat_armada)

                # 1. Pre-calculate Base Master Routes
                base_metrics_cache = {}
                for m_name, m_path in MASTER_ROUTES.items():
                    d_base, t_base, _ = get_route_details(m_path, route_matrix_dict)
                    if d_base is not None:
                        base_metrics_cache[m_name] = (d_base, t_base)

                if not base_metrics_cache:
                    st.error("⚠️ Master Node tidak ditemukan pada file route_matrix.csv.")
                    st.stop()

                # 2. Map B2B to Nearest Master Route
                b2b_route_mapping = {}
                for b2b_id in input_b2b_list:
                    best_m_route = None
                    min_metrics = (float('inf'), float('inf'))
                    for m_name, m_path in MASTER_ROUTES.items():
                        if m_name not in base_metrics_cache: continue
                        d_base, t_base = base_metrics_cache[m_name]
                        d_with_b2b, t_with_b2b, _ = get_route_details(m_path + [b2b_id], route_matrix_dict)
                        if d_with_b2b is not None:
                            curr_metrics = (d_with_b2b - d_base, t_with_b2b - t_base)
                            if curr_metrics < min_metrics:
                                min_metrics = curr_metrics
                                best_m_route = m_name
                    b2b_route_mapping[b2b_id] = best_m_route

                # 3. Grouping & Permutations
                grouped_by_route = {}
                for b2b_id, m_route in b2b_route_mapping.items():
                    grouped_by_route.setdefault(m_route, []).append(b2b_id)

                final_dispatch_list = []

                for m_route, b2bs in grouped_by_route.items():
                    if m_route is None:
                        st.error(f"⚠️ Gagal memetakan B2B: {', '.join(b2bs)} ke rute master manapun. Pastikan titik ini ada pada route_matrix.csv.")
                        continue
                    
                    master_path = MASTER_ROUTES[m_route]
                    for i in range(0, len(b2bs), max_b2b_per_trip):
                        chunk_b2bs = b2bs[i : i + max_b2b_per_trip]
                        best_seq, best_metrics, best_cost, best_time = None, (float('inf'),)*4, None, None

                        for p in itertools.permutations(chunk_b2bs):
                            full_path = master_path + list(p) + ["DMT"]
                            dist, dur, leg_durs = get_route_details(full_path, route_matrix_dict)
                            
                            if dist is not None and leg_durs is not None:
                                fin_min, load_min, wait_min, is_ot, w_dur, dt_info = evaluate_route_timeline(
                                    full_path, leg_durs, b2b_loading_dict, b2b_open_dict, dep_time_min, driver_start_min
                                )
                                tot_dur = dur + load_min + wait_min
                                c_all = calculate_cost(dist, tot_dur, cost_setting)
                                penalty = (5000 if is_ot else 0)
                                
                                curr_metrics = (penalty, dist, tot_dur, c_all[3])
                                if curr_metrics < best_metrics:
                                    best_metrics, best_seq, best_cost = curr_metrics, full_path, c_all
                                    best_time = (dur, load_min, wait_min, tot_dur, fin_min, is_ot, w_dur, dt_info)

                        if best_time is not None:
                            t_trv, t_load, t_wait, t_tot, fin_min, is_ot, w_dur, dt_info = best_time
                            
                            # Hitung Jam Overtime (Batas Waktu Kerja 9 Jam = 540 Menit)
                            overtime_hours = round(max(0.0, (w_dur - 540) / 60.0), 2)
                            status_str = "Overtime" if is_ot else "No Overtime"

                            final_dispatch_list.append({
                                "Jenis Armada": jenis_armada,
                                "Rute": " -> ".join(best_seq),
                                "Total Durasi Keseluruhan (Mnt)": round(t_tot, 1),
                                "BBM Cost (Rp)": best_cost[0],
                                "Driver Cost (Rp)": best_cost[1],
                                "Fleet Cost (Rp)": best_cost[2],
                                "Total Cost (Rp)": best_cost[3],
                                "Durasi Perjalanan + Unloading per titik": dt_info["combined_str"],
                                "Jam Finish": dt_info["dmt_finish_str"],
                                "Jumlah Overtime Driver (Jam)": overtime_hours,
                                "Status": status_str,
                            })

                if final_dispatch_list:
                    df_dispatch = pd.DataFrame(final_dispatch_list).sort_values(by=["Total Cost (Rp)"])
                    st.success("Alokasi Rute Selesai Dihitung!")
                    st.dataframe(df_dispatch, use_container_width=True)
                    st.download_button("Unduh CSV", df_dispatch.to_csv(index=False).encode("utf-8"), "jadwal_dispatch.csv", "text/csv")
                else:
                    st.warning("Tidak ada rute yang berhasil dihitung.")