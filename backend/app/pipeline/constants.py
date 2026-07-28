NOT_COLLECTED = "XXX_NOT_COLLECTED_XXX"

PLACEHOLDERS = {"", "0", "00000", "null", "none", "na", "n/a", "xxx_not_collected_xxx"}

INVALID_ADDRESSES = {
    "-", "0", "00", "000", "0000", "00000", "00000000000000000000", "198085468215",
    "00000000000000", "000000000000000", "198606284463", "198638377984", "1997199797",
    "07511943292", "07705597095", "07711195891", "07803571894", "07808357798",
    "07826951542", "07827336943", "11", "+9647508582831", "009647816045945", "009647830673883",
    "11519", "1213", "123", "JUNE", "12", "15", "198qgghjkk0", "197684137576", "2462305",
    "27 02 2034", "3453369", "5026655", "52292", "5332", "22", "6", "77", "88", "89", "90", "10", "90",
    "5213720403889723", "6928", "77", "377998", "JUNE", "000000000000", "000000000000000", "00000000000000000",
    "0000000000000000", "+9647508582831", "000000000000", "000000000000000", "00000000000",
    "0000000000000000", "00000000000000000000", "0000000000", "0000000000000", "A", "000000",
    "000000000000000000",
}

BAGHDAD_ADDRESS_POOL = [
    "ABI GHARIB AL-NASR WA AL-SALAM", "ADHAMIYA CHARA OMAR BN ABDULAZIZ",
    "AL AMRIYA KRB NADI ALKHTOT", "AL-AMARIYA HAY AL-FIRDAWS", "BAGHDAD - AL-MAAMOUN",
    "BAGHDAD AL-BALADIYAT", "MADINAT AL-SADR", "حي الخضراء", "الكاظمية", "بغداد الجديدة",
    "شارع فلسطين", "الدوره", "الحريه", "الصليخ", "بغداد حي العامل", "جسر ديالى", "المامون",
    "بغداد السيدية", "الغزاليه", "بغداد الشعله",
]

PROVINCE_ADDRESS_MAP = {
    "Baghdad": BAGHDAD_ADDRESS_POOL,
    "Al Anbar": ["الانبار القائم", "الرمادي", "الفلوجة"],
    "Al Basrah": ["قضاء المدينة", "خور الزبير"],
    "Al Munthana": ["سماوة حيل عسكري الجربوعية الثنية", "قضاء الخضر", "حي العسكري"],
    "Al Najaf": ["شارع المدينه", "حي الانصار", "كوفه حي ميسان"],
    "Al Quadisiya": ["قضاء الشاميه", "الديوانية القادسية", "حي الجامعه"],
    "Al Sulaymaniah": ["كلار شهيدان"],
    "Al Ta'amim": ["كركوك رحيم اوه", "كركوك حي الواسطي"],
    "Arbil": ["اربيل خبات", "اربيل قضاء خبات"],
    "Babil": ["قضاء القاسم", "القريه العصريه مكتب"],
    "Dahouk": ["عقرة مجمع ئازادي"],
    "Deyala": ["خان بني سعد", "ديالى بلدروز", "بعقوبة التحرير"],
    "Karbala": ["حي العسكري", "كربلاء حي الغدير", "كربلاء حي العامل"],
    "Kirkuk": ["ازادي الشورجة", "ازادى جديد", "ازدی ، جامع ازادی"],
    "Maysan": ["المجر الكبير", "قضاء الكحلاء", "حي الحسين القديم"],
    "Mousl (Nainawa)": ["قضاء تلعفر", "حي الانتصار", "تلعفر حي النور", "موصل نينوى", "موصل حي البكر"],
    "Salah Al Deen": ["صلاح الدين", "صلاحدين طوز خورماتوو جموري", "صلاح الدين قضاء بلد",
                       "صلاح الدين بلد", "سامراء حي المثنى"],
    "Thi Qar": ["قضاء الفجر", "ذي قار قلعة سكر", "قضاء الشطره"],
    "Wasit": ["واسط قضاء الحي", "كوت حي الحكيم"],
}

REPLACE_MAPPING_COLS = [
    "ACCOUNT_LAST_NAME", "ACCOUNT_FIRST_NAME", "ACCOUNT_MIDDLE_NAME", "DATE_OPENED",
    "ACCOUNT_HOLDER_DOB", "ACCOUNT_TYPE", "CARD_TYPE", "ACCOUNT_ADDRESS", "ADDRESS_CITY",
    "ADDRESS_PROVINCE", "ADDRESS_COUNTRY", "POSTAL_CODE", "PHONE_NUMBER", "EMAIL_ADDRESS",
    "ID_TYPE", "ID_NUMBER", "ID_COUNTRY", "NATIONALITY", "ISSUING_FI", "CARD_PROGRAM",
    "CARD_STATUS", "SECONDARY_CARD_TYPE",
]

CMS_UPDATE_COLS = ["CARD_NUMBER", "ACCOUNT_TYPE", "CARD_TYPE", "CARD_PROGRAM", "CARD_STATUS"]

# Columns for the CMS sheet added to the review workbook download -- deliberately
# a subset of CMS_UPDATE_COLS (no CARD_NUMBER). Kept separate from CMS_UPDATE_COLS
# because the live CMS upload/merge still requires the full 5-column set.
CMS_SHEET_COLS = ["ACCOUNT_TYPE", "CARD_TYPE", "CARD_PROGRAM", "CARD_STATUS"]

RAW_REQUIRED_COLS = list(dict.fromkeys(["ACCOUNT_NUMBER", *REPLACE_MAPPING_COLS, *CMS_UPDATE_COLS]))
