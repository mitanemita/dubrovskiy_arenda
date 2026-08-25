from aiogram.fsm.state import StatesGroup, State

class SurveyStates(StatesGroup):
    SELECT_TENANT = State()
    TENANT_ACTION = State()
    EDIT_TENANT = State()
    CONFIRM_DELETE = State()
    SELECT_DOCUMENT_TYPE = State()
    TENANT_ACTION1 = State()
    SEND_CONFIRMATION = State()
    ENTER_RENT_DATE = State()
    ENTER_RENT_DATE1 = State()
    SELECT_METER_COUNT = State()
    ENTER_METER_DATA = State()
    ENTER_PENI = State()
    PENI_DATE = State()

class AddTenant(StatesGroup):
    name = State()
    email = State()
    type = State()
    rent = State()
    inn = State()
    dog_num = State()
    adr_tow = State()
    dog_dat = State()
    recw_inf = State()

class AddUser(StatesGroup):
    name = State()
    type = State()
    inn = State()
    recw_inf = State()

class ProfileStates(StatesGroup):
    PROFILE_VIEW = State()
    SUBSCRIPTION_MENU = State()
    PHOTO_SIGNATURE = State()
    EDIT_MENU = State()
    EDIT_PROFILE = State()
    ENTER_AMOUNT = State()
    SELECT_SUBSCRIPTION = State()
    PROCESS_PAYMENT = State()

class MassSendStates(StatesGroup):
    CHOOSE_RECIPIENTS = State()
    ENTER_IDS = State()
    SELECT_TEMPLATE = State()
    CONFIRM_SEND = State()