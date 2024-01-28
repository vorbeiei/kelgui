import datetime
import math
import os
import sys
import configparser
import serial.tools.list_ports
import atexit
import ui_mainwindow
import time
import numpy
from PySide6 import QtWidgets, QtCore
from PySide6.QtCore import Signal, QTimer, Slot
from PySide6.QtWidgets import *
from PySide6.QtGui import QIcon, QColor
import pyqtgraph as pg
from pglive.kwargs import Crosshair, Axis
from pglive.sources.data_connector import DataConnector
from pglive.sources.live_plot import LiveLinePlot
from pglive.sources.live_axis import LiveAxis
from pglive.sources.live_axis_range import LiveAxisRange
from pglive.sources.live_plot_widget import LivePlotWidget
from kelctl import *
# from library.kelctl import * # only used for testing local changes in library

basedir = os.path.dirname(__file__)
running = False
start_time = datetime.datetime.now()
amps_list = [(0.0, 0.0)]  # List of tuple(time, amps)
volts_list = [(0.0, 0.0)]  # List of tuple(time, volts)
watts_list = [(0.0, 0.0)]  # List of tuple(time, watts)
ah_value = 0.0
wh_value = 0.0
configfile_name = "config.ini"
blocking_serial = False
previous_run_time = 0.0

setting_off_stop = True
setting_off_disconnect = False
setting_measure_interval = 0.5
setting_serial_debug = False
setting_baudrate = BaudRate.R115200
setting_crosshair = True
setting_graph_time = 30.0

load = KELSerial(None, setting_baudrate, setting_serial_debug, 0.0)

# TODO documentation


def exit_handler(window):  # Handling app shutdown to safely close connections and optionally stop load
    if running and setting_off_stop:
        load.input.off()
    window.thread.terminate()
    if load.is_open:
        load.close()


class ListCellDelegate(QItemDelegate):  # customize List mode table cells mostly to use QDoublespinbox inside cell
    def paint(self, painter, option, index):
        option.displayAlignment = QtCore.Qt.AlignmentFlag.AlignCenter
        QItemDelegate.paint(self, painter, option, index)

    def createEditor(self, parent, option, index):
        editor = QDoubleSpinBox(parent)
        editor.setMinimum(0)
        editor.setMaximum(999999)
        editor.setDecimals(4)
        return editor


class Worker(QtCore.QObject):  # separate thread for loop without locking up UI
    volt_label_update = Signal(str)
    mode_label_update = Signal(str)
    current_label_update = Signal(str)
    power_label_update = Signal(str)
    charge_label_update = Signal(str)
    charge_label_tip = Signal(str)
    runtime_label_update = Signal(str)
    start_button_update = Signal(str)
    start_button_icon = Signal(QIcon)
    start_button_stylesheet = Signal(str)
    start_button_checked = Signal(bool)
    energy_label_update = Signal(str)
    display_error = Signal(str, str)

    def __init__(self):
        global previous_run_time
        super(Worker, self).__init__()
        previous_run_time = 0.0

    @Slot()
    def work(self, data_connector_voltage: DataConnector, data_connector_current: DataConnector, data_connector_power: DataConnector):
        global blocking_serial, start_time, running, ah_value, wh_value, previous_run_time

        while blocking_serial:  # Maybe a bit hacky, but works(and is fairly simple) - blocking this thread from using serial connection while being used in other thread
            time.sleep(0.05)
        blocking_serial = True
        try:
            mode = load.function
            measured_voltage = load.measured_voltage
            measured_power = load.measured_power
            self.volt_label_update.emit(str(measured_voltage) + " V")
            self.mode_label_update.emit(mode.value)
            if load.input.get() == OnOffState.on:
                measured_current = load.measured_current
                current_run_time = datetime.datetime.now() - start_time
                volts_list.append((current_run_time.total_seconds(), measured_voltage))
                watts_list.append((current_run_time.total_seconds(), measured_power))
                data_connector_voltage.cb_append_data_point(measured_voltage, current_run_time.total_seconds())
                data_connector_power.cb_append_data_point(measured_power, current_run_time.total_seconds())
                wh_value = self.calculate_charge_energy(wh_value, current_run_time.total_seconds() - previous_run_time, measured_power)
                self.energy_label_update.emit(f'{wh_value:.5f}' + " Wh")
                self.current_label_update.emit(f'{measured_current:.5f}' + " A")
                self.power_label_update.emit(f'{measured_power:.5f}' + " W")

                if mode == Mode.battery:
                    battery_time = datetime.timedelta(minutes=load.get_batt_time())
                    self.charge_label_update.emit(f'{load.get_batt_cap():.5f}' + " Ah")
                    self.runtime_label_update.emit(
                        str(battery_time - datetime.timedelta(microseconds=battery_time.microseconds)))
                    self.charge_label_tip.emit("measured Value")
                else:
                    ah_value = self.calculate_charge_energy(ah_value, current_run_time.total_seconds() - previous_run_time, measured_current)
                    amps_list.append((current_run_time.total_seconds(), measured_current))
                    data_connector_current.cb_append_data_point(measured_current, current_run_time.total_seconds())
                    self.charge_label_update.emit(f'{ah_value:.5f}' + " Ah *")
                    self.runtime_label_update.emit(
                        str(current_run_time - datetime.timedelta(microseconds=current_run_time.microseconds)))
                    self.charge_label_tip.emit("Calculated estimate - not measured")

                if not running:
                    running = True
                    start_time = datetime.datetime.now()
                    self.start_button_update.emit("Stop")
                    self.start_button_icon.emit(QIcon(os.path.join(basedir, "stop.png")))
                    self.start_button_stylesheet.emit("color: rgb(170, 0, 0);")
                    self.start_button_checked.emit(True)

                previous_run_time = current_run_time.total_seconds()
            else:
                if running:
                    running = False
                    self.start_button_update.emit("Start")
                    self.start_button_icon.emit(QIcon(os.path.join(basedir, "play.png")))
                    self.start_button_stylesheet.emit("color: rgb(0, 170, 0);")
                    self.start_button_checked.emit(False)
                    self.current_label_update.emit("0 A")
                    self.power_label_update.emit("0 W")
            blocking_serial = False
        except (serial.serialutil.PortNotOpenError, ValueError, AttributeError) as ex:
            blocking_serial = False
            self.display_error.emit("Update Error", "Error during updating values:\n" + str(ex) + "\nProbably error on device, clear error on device(on device or by setting different mode) and disconnect/reconnect.")

    def calculate_charge_energy(self, amp_watt_hour_value: float, runtime: float, last_value: float):
        charge_energy = amp_watt_hour_value
        charge_energy += (last_value * runtime) / 3600

        return charge_energy


class MainWindow(QMainWindow, ui_mainwindow.Ui_MainWindow):
    do_work = Signal(DataConnector, DataConnector, DataConnector)

    def __init__(self):
        super(MainWindow, self).__init__()
        self.setupUi(self)  # gets defined in the UI file
        self.setWindowTitle("KEL103 Control")
        self.btn_connect.setChecked(False)

        # Connecting to all the signals from Ui-elements
        self.btn_refreshPorts.clicked.connect(self.refresh_ports)
        self.btn_connect.clicked.connect(self.pressed_connect_btn)
        self.btn_setStd.clicked.connect(self.pressed_set_std_btn)
        self.cmbBox_stdModes.currentIndexChanged.connect(self.selected_std_mode_changed)
        self.btn_startStop.clicked.connect(self.pressed_start_btn)
        self.btn_getLimits.clicked.connect(self.get_limits)
        self.val_maxResLimit.valueChanged.connect(lambda: self.val_maxResLimit.setStyleSheet("color: red;"))
        self.val_maxPowerLimit.valueChanged.connect(lambda: self.val_maxPowerLimit.setStyleSheet("color: red;"))
        self.val_maxVoltLimit.valueChanged.connect(lambda: self.val_maxVoltLimit.setStyleSheet("color: red;"))
        self.val_maxCurrLimit.valueChanged.connect(lambda: self.val_maxCurrLimit.setStyleSheet("color: red;"))
        self.btn_resetLimits.clicked.connect(self.reset_limits)
        self.btn_setResLimit.clicked.connect(lambda: self.set_limits(self.btn_setResLimit))
        self.btn_setCurrLimit.clicked.connect(lambda: self.set_limits(self.btn_setCurrLimit))
        self.btn_setPowerLimit.clicked.connect(lambda: self.set_limits(self.btn_setPowerLimit))
        self.btn_setVoltLimit.clicked.connect(lambda: self.set_limits(self.btn_setVoltLimit))
        self.btn_batSet.clicked.connect(self.set_battery)
        self.btn_bat_recall.clicked.connect(self.recall_battery)
        self.btn_clearOut.clicked.connect(self.clear_lists)
        self.btn_factory_reset.clicked.connect(self.factory_reset)
        self.btn_init_saves.clicked.connect(self.init_saves)
        self.btn_export.clicked.connect(lambda: self.export_data(self.cmbBox_outGraphSel.currentText()))
        self.btn_get_settings.clicked.connect(self.get_settings)
        self.btn_set_settings.clicked.connect(self.set_settings)
        self.btn_ocp_set.clicked.connect(self.set_ocp)
        self.btn_ocp_recall.clicked.connect(self.recall_ocp)
        self.btn_ocp_validate.clicked.connect(self.validate_ocp)
        self.btn_opp_set.clicked.connect(self.set_opp)
        self.btn_opp_recall.clicked.connect(self.recall_opp)
        self.btn_opp_validate.clicked.connect(self.validate_opp)
        self.btn_dcv_validate.clicked.connect(self.validate_dcv)
        self.btn_dcv_set.clicked.connect(self.set_dcv)
        self.btn_dcc_validate.clicked.connect(self.validate_dcc)
        self.btn_dcc_set.clicked.connect(self.set_dcc)
        self.btn_dcr_set.clicked.connect(self.set_dcr)
        self.btn_dcr_validate.clicked.connect(self.validate_dcr)
        self.btn_dcp_set.clicked.connect(self.set_dcp)
        self.btn_dcp_validate.clicked.connect(self.validate_dcp)
        self.btn_pulse_set.clicked.connect(self.set_pulse)
        self.btn_pulse_validate.clicked.connect(self.validate_pulse)
        self.btn_toggle_set.clicked.connect(self.set_toggle)
        self.btn_toggle_validate.clicked.connect(self.validate_toggle)
        self.btn_trigger.clicked.connect(lambda: load.trigger())
        self.btn_list_set.clicked.connect(self.set_list)
        self.btn_list_validate.clicked.connect(self.validate_list)
        self.btn_list_recall.clicked.connect(self.recall_list)
        self.btn_list_clear_all.clicked.connect(self.table_list.clearContents)
        self.btn_list_clear_mark.clicked.connect(self.clear_marked_list)
        self.btn_save_settings.clicked.connect(self.save_settings)
        self.btn_memory_save.clicked.connect(lambda: load.memories[self.val_memory_slot.value()].save())
        self.btn_memory_recall.clicked.connect(lambda: load.memories[self.val_memory_slot.value()].recall())
        self.chk_show_current.stateChanged.connect(lambda: self.plot_curve_current.setVisible(self.chk_show_current.isChecked()))
        self.chk_show_power.stateChanged.connect(lambda: self.plot_curve_power.setVisible(self.chk_show_power.isChecked()))
        self.chk_show_voltage.stateChanged.connect(lambda: self.plot_curve_voltage.setVisible(self.chk_show_voltage.isChecked()))

        # Set up table in List mode settings
        self.table_list.setItemDelegate(ListCellDelegate(self.table_list))
        header = self.table_list.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)

        self.refresh_ports()

        # connecting to all signals from worker thread
        self.worker = Worker()
        self.worker.volt_label_update.connect(self.lbl_volt_out.setText)
        self.worker.mode_label_update.connect(self.lbl_mode.setText)
        self.worker.current_label_update.connect(self.lbl_current_out.setText)
        self.worker.power_label_update.connect(self.lbl_power_out.setText)
        self.worker.charge_label_update.connect(self.lbl_charge_out.setText)
        self.worker.charge_label_tip.connect(self.lbl_charge_out.setToolTip)
        self.worker.runtime_label_update.connect(self.lbl_runtime_out.setText)
        self.worker.start_button_update.connect(self.btn_startStop.setText)
        self.worker.start_button_icon.connect(self.btn_startStop.setIcon)
        self.worker.start_button_stylesheet.connect(self.btn_startStop.setStyleSheet)
        self.worker.start_button_checked.connect(self.btn_startStop.setChecked)
        self.worker.energy_label_update.connect(self.lbl_energy_out.setText)
        self.worker.display_error.connect(self.display_error_thread)

        self.btn_startStop.setIcon(QIcon(os.path.join(basedir, "play.png")))

        # Setting up graph
        kwargs = {Crosshair.ENABLED: True,
                  Crosshair.LINE_PEN: pg.mkPen(color="red", width=1),
                  Crosshair.TEXT_KWARGS: {"color": "yellow"}}
        top_axis = LiveAxis("bottom", axisPen="white", textPen="white", **{Axis.TICK_FORMAT: Axis.DURATION})
        self.plot_widget = LivePlotWidget(parent=self, axisItems={'top': top_axis}, x_range_controller=LiveAxisRange(roll_on_tick=1, offset_left=30, offset_right=0.1), **kwargs)
        self.plot_widget.x_range_controller.crop_left_offset_to_data = True
        self.plot_curve_voltage = LiveLinePlot(pen="green")
        self.plot_curve_current = LiveLinePlot(pen="red")
        self.plot_curve_power = LiveLinePlot(pen=QColor(0, 170, 255))
        self.plot_widget.addItem(self.plot_curve_voltage)
        self.plot_widget.addItem(self.plot_curve_power)
        self.plot_widget.addItem(self.plot_curve_current)
        self.data_connector_voltage = DataConnector(self.plot_curve_voltage, update_rate=4)
        self.data_connector_current = DataConnector(self.plot_curve_current, update_rate=4)
        self.data_connector_power = DataConnector(self.plot_curve_power, update_rate=4)
        self.layout_graph.addWidget(self.plot_widget)

        # Move worker to another thread and setup timer to call worker
        self.thread = QtCore.QThread(self)
        self.worker.moveToThread(self.thread)
        self.do_work.connect(self.worker.work)
        self.timer = QTimer()
        self.timer.setInterval(1)
        self.timer.timeout.connect(lambda: self.do_work.emit(self.data_connector_voltage, self.data_connector_current, self.data_connector_power))

        self.read_settings()

        self.thread.start()

    def read_settings(self):
        global setting_off_stop, setting_measure_interval, setting_serial_debug, setting_baudrate, setting_crosshair, setting_graph_time, setting_off_disconnect

        # Read settings from existing config file, otherwise set one up with default settings
        if not os.path.isfile(configfile_name):  # if no config exists - create new one
            config = configparser.ConfigParser()
            cfgfile = open(configfile_name, 'w')
            config.add_section('Settings')
            config.set('Settings', 'off_close', 'True')
            config.set('Settings', 'off_disconnect', 'False')
            config.set('Settings', 'measure_interval', self.val_measure_interval.value().__str__())
            config.set('Settings', 'serial_debug', 'False')
            config.set('Settings', 'baudrate', '115200')
            config.set('Settings', 'crosshair', 'True')
            config.set('Settings', 'graph_time', '30.0')
            self.chk_off_close.setChecked(True)
            self.chk_off_disconnect.setChecked(False)
            self.val_measure_interval.setValue(self.val_measure_interval.value())
            self.chk_serial_debug.setChecked(False)
            self.cmb_baudrate_soft.setCurrentText("115200")
            self.chk_crosshair.setChecked(True)
            self.val_graph_time.setValue(30)
            config.write(cfgfile)
            cfgfile.close()
        else:
            config = configparser.ConfigParser()  # if exists open and read file into variables
            config.read(configfile_name)
            setting_off_stop = config.getboolean('Settings', 'off_close')
            setting_off_disconnect = config.getboolean('Settings', 'off_disconnect')
            setting_measure_interval = config.getfloat('Settings', 'measure_interval')
            setting_serial_debug = config.getboolean('Settings', 'serial_debug')
            setting_baudrate = BaudRate(config.getint('Settings', 'baudrate'))
            setting_crosshair = config.getboolean('Settings', 'crosshair')
            setting_graph_time = config.getfloat('Settings', 'graph_time')
            self.chk_off_close.setChecked(setting_off_stop)
            self.chk_off_disconnect.setChecked(setting_off_disconnect)
            self.val_measure_interval.setValue(setting_measure_interval)
            self.chk_serial_debug.setChecked(setting_serial_debug)
            self.cmb_baudrate_soft.setCurrentText(setting_baudrate.b.__str__())
            self.timer.setInterval((setting_measure_interval * 1000).__int__())
            self.chk_crosshair.setChecked(setting_crosshair)
            self.plot_widget.crosshair_enabled = setting_crosshair
            self.val_graph_time.setValue(setting_graph_time)
            self.plot_widget.x_range_controller.offset_left = setting_graph_time

    def save_settings(self):  # Save settings to config file and then read back settings(which also sets saved settings)
        config = configparser.ConfigParser()
        config.read(configfile_name)
        config.set('Settings', 'off_close', self.chk_off_close.isChecked().__str__())
        config.set('Settings', 'off_disconnect', self.chk_off_disconnect.isChecked().__str__())
        config.set('Settings', 'measure_interval', self.val_measure_interval.value().__str__())
        config.set('Settings', 'serial_debug', self.chk_serial_debug.isChecked().__str__())
        config.set('Settings', 'baudrate', self.cmb_baudrate_soft.currentText())
        config.set('Settings', 'crosshair', self.chk_crosshair.isChecked().__str__())
        config.set('Settings', 'graph_time', self.val_graph_time.value().__str__())
        cfgfile = open(configfile_name, 'w')
        config.write(cfgfile)
        cfgfile.close()

        self.read_settings()

    def clear_lists(self):  # Clear data-logs and graph
        volts_list.clear()
        amps_list.clear()
        watts_list.clear()
        self.data_connector_power.clear()
        self.data_connector_current.clear()
        self.data_connector_voltage.clear()

    def selected_std_mode_changed(self):  # When dropdown in basic mode section changes also change suffix in SpinBox
        match self.cmbBox_stdModes.currentIndex():
            case 0:
                self.val_stdSet.setSuffix(" A")
            case 1:
                self.val_stdSet.setSuffix(" V")
            case 2:
                self.val_stdSet.setSuffix(" \u03A9")
            case 3:
                self.val_stdSet.setSuffix(" W")
            case 4:
                self.val_stdSet.setSuffix(" Short")

    # Maybe a bit hacky, but works(and is fairly simple) - functions for blocking this thread from using serial connection while being used in other thread
    def wait_and_set_block(self):
        global blocking_serial
        while blocking_serial:
            time.sleep(0.05)
        blocking_serial = True

    def unset_block(self):
        global blocking_serial
        blocking_serial = False

    def pressed_set_std_btn(self):  # Setting basic mode based on which item is selected in dropdown
        try:
            self.wait_and_set_block()
            match self.cmbBox_stdModes.currentIndex():
                case 0:
                    load.current = self.val_stdSet.value()
                case 1:
                    load.voltage = self.val_stdSet.value()
                case 2:
                    load.resistance = self.val_stdSet.value()
                case 3:
                    load.power = self.val_stdSet.value()
                case 4:
                    load.function = Mode.short
            self.unset_block()
        except (serial.serialutil.PortNotOpenError, ValueError) as ex:
            self.unset_block()
            self.display_error(ex)
            return

    def pressed_connect_btn(self):  # Connect or Disconnect from load
        global load

        if not self.btn_connect.isChecked():
            print("Disconnect")
            self.timer.stop()
            if setting_off_disconnect:
                load.input.off()
                self.btn_startStop.setText("Start")
                self.btn_startStop.setIcon(QIcon(os.path.join(basedir, "play.png")))
                self.btn_startStop.setStyleSheet("color: rgb(0, 170, 0);")
                self.btn_startStop.setChecked(False)
            load.close()
            self.lbl_model.setText("Model:")
            self.btn_connect.setText("Connect")

        else:
            print("Connect")
            try:
                load = KELSerial(self.cmbBox_ports.currentText(), setting_baudrate, setting_serial_debug)
                model = load.model
            except (serial.serialutil.PortNotOpenError, ValueError) as ex:
                self.display_error(ex)
                return
            print(model)
            self.lbl_model.setText("Model: " + model)
            self.btn_connect.setText("Disconnect")
            self.get_limits()
            self.timer.start()

    def refresh_ports(self):  # Refresh list of serial ports and automatically select first port which could be load based on name
        self.cmbBox_ports.clear()
        ports = serial.tools.list_ports.comports()
        for p in ports:
            print(p)
            self.cmbBox_ports.addItem(p.device)
            if p.description.__contains__("KORAD"):
                self.cmbBox_ports.setCurrentText(p.device)

    def pressed_start_btn(self):
        global start_time, running

        if self.btn_startStop.isChecked():
            try:
                load.input.on()
            except (serial.serialutil.PortNotOpenError, ValueError) as ex:
                self.display_error(ex)
                return

            self.btn_startStop.setText("Stop")
            self.btn_startStop.setIcon(QIcon(os.path.join(basedir, "stop.png")))
            self.btn_startStop.setStyleSheet("color: rgb(170, 0, 0);")
            start_time = datetime.datetime.now()
            watts_list.clear()
            amps_list.clear()
            volts_list.clear()
            self.data_connector_voltage.clear()
            self.data_connector_current.clear()
            self.data_connector_power.clear()
            running = True

        else:
            try:
                load.input.off()
            except (serial.serialutil.PortNotOpenError, ValueError) as ex:
                self.display_error(ex)
                return

            self.btn_startStop.setText("Start")
            self.btn_startStop.setIcon(QIcon(os.path.join(basedir, "play.png")))
            self.btn_startStop.setStyleSheet("color: rgb(0, 170, 0);")
            self.lbl_current_out.setText("0 A")
            self.lbl_power_out.setText("0 W")
            running = False

    def get_limits(self):
        try:
            self.wait_and_set_block()
            self.val_maxVoltLimit.setValue(load.settings.voltage_limit)
            self.val_maxCurrLimit.setValue(load.settings.current_limit)
            self.val_maxResLimit.setValue(load.settings.resistance_limit)
            self.val_maxPowerLimit.setValue(load.settings.power_limit)
            self.unset_block()
        except serial.serialutil.PortNotOpenError as ex:
            self.unset_block()
            self.display_error(ex)
            return

        self.val_maxVoltLimit.setStyleSheet("")
        self.val_maxCurrLimit.setStyleSheet("")
        self.val_maxPowerLimit.setStyleSheet("")
        self.val_maxResLimit.setStyleSheet("")

    def reset_limits(self):
        try:
            load.settings.voltage_limit = 120
            load.settings.current_limit = 30
            load.settings.power_limit = 300
            load.settings.resistance_limit = 7500
        except serial.serialutil.PortNotOpenError as ex:
            self.display_error(ex)
            return

        self.val_maxVoltLimit.setStyleSheet("")
        self.val_maxCurrLimit.setStyleSheet("")
        self.val_maxPowerLimit.setStyleSheet("")
        self.val_maxResLimit.setStyleSheet("")

        self.get_limits()

    def set_limits(self, btn_object: QPushButton):
        try:
            match btn_object:
                case self.btn_setPowerLimit:
                    load.settings.power_limit = self.val_maxPowerLimit.value()
                    self.val_maxPowerLimit.setStyleSheet("")
                case self.btn_setResLimit:
                    load.settings.resistance_limit = self.val_maxResLimit.value()
                    self.val_maxResLimit.setStyleSheet("")
                case self.btn_setVoltLimit:
                    load.settings.voltage_limit = self.val_maxVoltLimit.value()
                    self.val_maxVoltLimit.setStyleSheet("")
                case self.btn_setCurrLimit:
                    load.settings.current_limit = self.val_maxCurrLimit.value()
                    self.val_maxCurrLimit.setStyleSheet("")
        except serial.serialutil.PortNotOpenError as ex:
            self.display_error(ex)
            return

    def set_battery(self):
        battery_list = BattList(self.val_battery_slot.value(), self.val_battery_current.value(),
                                self.val_battery_current.value(), self.val_battery_voltage.value(),
                                self.val_battery_capacity.value(),
                                self.val_battery_hours.value() * 60 + self.val_battery_minutes.value() + self.val_battery_seconds.value() / 60)
        try:
            load.set_batt(battery_list)
        except Exception as ex:
            self.display_error(ex)
            return

    def recall_battery(self):
        try:
            self.wait_and_set_block()
            battery_list = load.get_batt(self.val_battery_slot.value())
            self.unset_block()
        except (serial.serialutil.PortNotOpenError, ValueError) as ex:
            self.unset_block()
            self.display_error(ex)
            return

        self.val_battery_voltage.setValue(battery_list.cutoff_voltage)
        self.val_battery_current.setValue(battery_list.discharge_current)
        self.val_battery_capacity.setValue(battery_list.cutoff_capacity)
        battery_time = battery_list.cutoff_time
        self.val_battery_hours.setValue(math.trunc(battery_time / 60))
        battery_time -= math.trunc(battery_time / 60) * 60
        self.val_battery_minutes.setValue(math.trunc(battery_time))
        self.val_battery_seconds.setValue(round(battery_time % 1 * 60))

    def export_data(self, value_type: str):  # export recorded data as csv file
        file_name = QFileDialog.getSaveFileName(self, "Export to...", "", "csv (*.csv)")[0]
        value_list = volts_list
        match value_type:
            case "Voltage":
                value_list = volts_list
            case "Current":
                value_list = amps_list
            case "Power":
                value_list = watts_list
        if not file_name == "":
            if not file_name.endswith(".csv"):
                file_name += ".csv"
            numpy.savetxt(fname=file_name, X=value_list, fmt='%f', delimiter=",", header="Time,Value", comments="")

    def factory_reset(self):  # Perform factory reset after confirmation from dialog
        confirmation_box = QMessageBox.warning(self.parent(), "Confirm Factory Reset",
                                               "Confirm Factory Reset?\n" + "This might cause interruption of connection!",
                                               QMessageBox.StandardButton.Cancel | QMessageBox.StandardButton.Ok,
                                               QMessageBox.StandardButton.Cancel)
        try:
            if confirmation_box == QMessageBox.StandardButton.Ok:
                load.settings.factoryreset()
        except serial.serialutil.PortNotOpenError as ex:
            self.display_error(ex)
            return

    def get_settings(self):
        try:
            self.groupBox_settings.setTitle("Device Settings - Updating settings from device")
            QtCore.QCoreApplication.processEvents()
            self.wait_and_set_block()
            self.cmb_baudrate.setCurrentText(str(load.settings.baudrate.b))
            self.chk_beep.setChecked(load.settings.beep.get().value)
            self.chk_lock.setChecked(load.settings.lock.get().value)
            self.chk_trigger.setChecked(load.settings.trigger.get().value)
            self.chk_comp.setChecked(load.settings.compensation.get().value)
            self.chk_dhcp.setChecked(load.settings.dhcp.get().value)
            self.val_ip_address.setText(load.settings.ipaddress)
            self.val_subnetmask.setText(load.settings.subnetmask)
            self.val_gateway.setText(load.settings.gateway)
            self.val_mac_address.setText(load.settings.macaddress)
            self.val_device_port.setValue(load.settings.port)
            self.unset_block()
            self.groupBox_settings.setTitle("Device Settings")
        except serial.serialutil.PortNotOpenError as ex:
            self.unset_block()
            self.display_error(ex)
            self.groupBox_settings.setTitle("Device Settings")
            return

    def set_settings(self):
        try:
            self.groupBox_settings.setTitle("Device Settings - Saving settings to device")
            QtCore.QCoreApplication.processEvents()
            load.settings.setting_baudrate = BaudRate(int(self.cmb_baudrate.currentText()))
            load.settings.beep.on() if self.chk_beep.isChecked() else load.settings.beep.off()
            load.settings.lock.on() if self.chk_lock.isChecked() else load.settings.lock.off()
            load.settings.trigger.on() if self.chk_trigger.isChecked() else load.settings.trigger.off()
            load.settings.compensation.on() if self.chk_comp.isChecked() else load.settings.compensation.off()
            load.settings.ipaddress = self.val_ip_address.text()
            load.settings.subnetmask = self.val_subnetmask.text()
            load.settings.gateway = self.val_gateway.text()
            load.settings.macaddress = self.val_mac_address.text()
            load.settings.port = self.val_device_port.value()
            load.settings.dhcp.on() if self.chk_dhcp.isChecked() else load.settings.dhcp.off()
            self.groupBox_settings.setTitle("Device Settings")
        except serial.serialutil.PortNotOpenError as ex:
            self.display_error(ex)
            self.groupBox_settings.setTitle("Device Settings")
            return

    def set_ocp(self):
        ocp_list = OCPList(self.val_ocp_slot.value(), self.val_ocp_on_voltage.value(),
                           self.val_ocp_on_delay_seconds.value(), self.val_ocp_initial_current.value(),
                           self.val_ocp_initial_current.value(), self.val_ocp_step_current.value(),
                           self.val_ocp_step_delay_seconds.value(), self.val_ocp_off_current.value(),
                           self.val_ocp_test_voltage.value(), self.val_ocp_max_over_current.value(),
                           self.val_ocp_min_over_current.value())
        try:
            load.set_ocp(ocp_list)
        except Exception as ex:
            self.display_error(ex)
            return

    def recall_ocp(self):
        try:
            self.wait_and_set_block()
            ocp_list = load.get_ocp(self.val_ocp_slot.value())
            self.unset_block()
        except (serial.serialutil.PortNotOpenError, ValueError) as ex:
            self.unset_block()
            self.display_error(ex)
            return
        self.val_ocp_on_voltage.setValue(ocp_list.on_voltage)
        self.val_ocp_on_delay_seconds.setValue(ocp_list.on_delay)
        self.val_ocp_initial_current.setValue(ocp_list.initial_current)
        self.val_ocp_step_current.setValue(ocp_list.step_current)
        self.val_ocp_step_delay_seconds.setValue(ocp_list.step_delay)
        self.val_ocp_off_current.setValue(ocp_list.off_current)
        self.val_ocp_test_voltage.setValue(ocp_list.ocp_voltage)
        self.val_ocp_max_over_current.setValue(ocp_list.max_overcurrent)
        self.val_ocp_min_over_current.setValue(ocp_list.min_overcurrent)

    def display_error(self, ex: Exception):
        values = ""
        if isinstance(ex, ValueOutOfLimitError):
            values = "Affected value " + ex.value.__str__() + " being out of limit of " + ex.limit.__str__()
        QMessageBox.critical(self, "Validation error", "Error during validation:\n" + str(ex) + "\n" + values)

    def display_error_thread(self, title: str, msg: str):  # For displaying error from worker thread
        self.timer.stop()
        QMessageBox.critical(self, title, msg)

    def validate_ocp(self):
        ocp_list = OCPList(self.val_ocp_slot.value(), self.val_ocp_on_voltage.value(),
                           self.val_ocp_on_delay_seconds.value(), self.val_ocp_initial_current.value(),
                           self.val_ocp_initial_current.value(), self.val_ocp_step_current.value(),
                           self.val_ocp_step_delay_seconds.value(), self.val_ocp_off_current.value(),
                           self.val_ocp_test_voltage.value(), self.val_ocp_max_over_current.value(),
                           self.val_ocp_min_over_current.value())
        try:
            ocp_list.validate()
        except Exception as ex:
            self.display_error(ex)
        else:
            QMessageBox.information(self, "Validation OK", "Validation passed")

    def set_opp(self):
        opp_list = OPPList(self.val_opp_slot.value(), self.val_opp_on_voltage.value(),
                           self.val_opp_on_delay_seconds.value(), self.val_opp_current_range.value(),
                           self.val_opp_initial_power.value(), self.val_opp_step_power.value(),
                           self.val_opp_step_delay_seconds.value(), self.val_opp_off_power.value(),
                           self.val_opp_test_voltage.value(), self.val_opp_max_over_power.value(),
                           self.val_opp_min_over_power.value())
        try:
            load.set_opp(opp_list)
        except Exception as ex:
            self.display_error(ex)
            return

    def recall_opp(self):
        try:
            self.wait_and_set_block()
            opp_list = load.get_opp(self.val_opp_slot.value())
            self.unset_block()
        except (serial.serialutil.PortNotOpenError, ValueError) as ex:
            self.unset_block()
            self.display_error(ex)
            return
        self.val_opp_on_voltage.setValue(opp_list.on_voltage)
        self.val_opp_on_delay_seconds.setValue(opp_list.on_delay)
        self.val_opp_current_range.setValue(opp_list.current_range)
        self.val_opp_initial_power.setValue(opp_list.initial_power)
        self.val_opp_step_power.setValue(opp_list.step_power)
        self.val_opp_step_delay_seconds.setValue(opp_list.step_delay)
        self.val_opp_off_power.setValue(opp_list.off_power)
        self.val_opp_test_voltage.setValue(opp_list.opp_voltage)
        self.val_opp_max_over_power.setValue(opp_list.max_overpower)
        self.val_opp_min_over_power.setValue(opp_list.min_overpower)

    def validate_opp(self):
        opp_list = OPPList(self.val_opp_slot.value(), self.val_opp_on_voltage.value(),
                           self.val_opp_on_delay_seconds.value(), self.val_opp_current_range.value(),
                           self.val_opp_initial_power.value(), self.val_opp_step_power.value(),
                           self.val_opp_step_delay_seconds.value(), self.val_opp_off_power.value(),
                           self.val_opp_test_voltage.value(), self.val_opp_max_over_power.value(),
                           self.val_opp_min_over_power.value())
        try:
            opp_list.validate()
        except Exception as ex:
            self.display_error(ex)
        else:
            QMessageBox.information(self, "Validation OK", "Validation passed")

    def validate_dcv(self):
        dcv_list = CVList(self.val_dcv_voltage_1.value(), self.val_dcv_voltage_2.value(),
                          self.val_dcv_frequency.value(), self.val_dcv_duty.value())
        try:
            self.wait_and_set_block()
            dcv_list.validate(load.settings.voltage_limit)
            self.unset_block()
        except Exception as ex:
            self.unset_block()
            self.display_error(ex)
        else:
            QMessageBox.information(self, "Validation OK", "Validation passed")

    def set_dcv(self):
        dcv_list = CVList(self.val_dcv_voltage_1.value(), self.val_dcv_voltage_2.value(),
                          self.val_dcv_frequency.value(), self.val_dcv_duty.value())
        try:
            self.wait_and_set_block()
            load.set_dynamic_mode(dcv_list)
            self.unset_block()
        except Exception as ex:
            self.unset_block()
            self.display_error(ex)
            return

    def validate_dcc(self):
        dcc_list = CCList(self.val_dcc_slope_1.value(), self.val_dcc_slope_2.value(), self.val_dcc_current_1.value(),
                          self.val_dcc_current_2.value(), self.val_dcc_frequency.value(), self.val_dcc_duty.value())

        try:
            self.wait_and_set_block()
            dcc_list.validate(load.settings.current_limit)
            self.unset_block()
        except Exception as ex:
            self.unset_block()
            self.display_error(ex)
        else:
            QMessageBox.information(self, "Validation OK", "Validation passed")

    def set_dcc(self):
        dcc_list = CCList(self.val_dcc_slope_1.value(), self.val_dcc_slope_2.value(), self.val_dcc_current_1.value(),
                          self.val_dcc_current_2.value(), self.val_dcc_frequency.value(), self.val_dcc_duty.value())
        try:
            self.wait_and_set_block()
            load.set_dynamic_mode(dcc_list)
            self.unset_block()
        except Exception as ex:
            self.unset_block()
            self.display_error(ex)
            return

    def validate_dcr(self):
        dcr_list = CRList(self.val_dcr_resistance_1.value(), self.val_dcr_resistance_2.value(),
                          self.val_dcr_frequency.value(), self.val_dcr_duty.value())
        try:
            self.wait_and_set_block()
            dcr_list.validate(load.settings.resistance_limit)
            self.unset_block()
        except Exception as ex:
            self.unset_block()
            self.display_error(ex)
        else:
            QMessageBox.information(self, "Validation OK", "Validation passed")

    def set_dcr(self):
        dcr_list = CRList(self.val_dcr_resistance_1.value(), self.val_dcr_resistance_2.value(),
                          self.val_dcr_frequency.value(), self.val_dcr_duty.value())
        try:
            self.wait_and_set_block()
            load.set_dynamic_mode(dcr_list)
            self.unset_block()
        except Exception as ex:
            self.unset_block()
            self.display_error(ex)
            return

    def validate_dcp(self):
        dcp_list = CWList(self.val_dcp_power_1.value(), self.val_dcp_power_2.value(), self.val_dcp_frequency.value(),
                          self.val_dcp_duty.value())
        try:
            self.wait_and_set_block()
            dcp_list.validate(load.settings.power_limit)
            self.unset_block()
        except Exception as ex:
            self.unset_block()
            self.display_error(ex)
        else:
            QMessageBox.information(self, "Validation OK", "Validation passed")

    def set_dcp(self):
        dcp_list = CWList(self.val_dcp_power_1.value(), self.val_dcp_power_2.value(), self.val_dcp_frequency.value(),
                          self.val_dcp_duty.value())
        try:
            self.wait_and_set_block()
            load.set_dynamic_mode(dcp_list)
            self.unset_block()
        except Exception as ex:
            self.unset_block()
            self.display_error(ex)
            return

    def validate_pulse(self):
        pulse_list = PulseList(self.val_pulse_slope_1.value(), self.val_pulse_slope_2.value(),
                               self.val_pulse_current_1.value(), self.val_pulse_current_2.value(),
                               self.val_pulse_duration.value())

        try:
            self.wait_and_set_block()
            pulse_list.validate(load.settings.current_limit)
            self.unset_block()
        except Exception as ex:
            self.unset_block()
            self.display_error(ex)
        else:
            QMessageBox.information(self, "Validation OK", "Validation passed")

    def set_pulse(self):
        pulse_list = PulseList(self.val_pulse_slope_1.value(), self.val_pulse_slope_2.value(),
                               self.val_pulse_current_1.value(), self.val_pulse_current_2.value(),
                               self.val_pulse_duration.value())
        try:
            self.wait_and_set_block()
            load.set_dynamic_mode(pulse_list)
            self.unset_block()
        except Exception as ex:
            self.unset_block()
            self.display_error(ex)
            return

    def validate_toggle(self):
        toggle_list = ToggleList(self.val_toggle_slope_1.value(), self.val_toggle_slope_2.value(),
                                 self.val_toggle_current_1.value(), self.val_toggle_current_2.value())

        try:
            self.wait_and_set_block()
            toggle_list.validate(load.settings.current_limit)
            self.unset_block()
        except Exception as ex:
            self.unset_block()
            self.display_error(ex)
        else:
            QMessageBox.information(self, "Validation OK", "Validation passed")

    def set_toggle(self):
        toggle_list = ToggleList(self.val_toggle_slope_1.value(), self.val_toggle_slope_2.value(),
                                 self.val_toggle_current_1.value(), self.val_toggle_current_2.value())
        try:
            self.wait_and_set_block()
            load.set_dynamic_mode(toggle_list)
            self.unset_block()
        except Exception as ex:
            self.unset_block()
            self.display_error(ex)
            return

    def table_row_incomplete(self, row: int):  # to check if all rows of table are complete
        all_empty = True
        for column in range(self.table_list.columnCount()):
            all_empty = all_empty if isinstance(self.table_list.item(row, column), type(None)) else False

        return True if all_empty is False else False

    def set_list(self):
        steps = []
        highest_current = 0
        for r in range(self.table_list.rowCount()):
            if not (isinstance(self.table_list.item(r, 0), type(None)) or isinstance(self.table_list.item(r, 1), type(None)) or isinstance(self.table_list.item(r, 2), type(None))):
                current = float(self.table_list.item(r, 0).text())
                highest_current = current if current > highest_current else highest_current
                current_slope = float(self.table_list.item(r, 1).text())
                duration = float(self.table_list.item(r, 2).text())
                steps.append(ListStep(current, current_slope, duration))
            elif self.table_row_incomplete(r):
                QMessageBox.warning(self, "Incomplete entry", "Incomplete entry on row " + (r + 1).__str__())
                return

        try:
            load.set_list(LoadList(self.val_list_slot.value(), highest_current, steps, self.val_list_loops.value()))
        except Exception as ex:
            self.display_error(ex)
            return

    def validate_list(self):
        steps = []
        highest_current = 0
        for r in range(self.table_list.rowCount()):
            if not (isinstance(self.table_list.item(r, 0), type(None)) or isinstance(self.table_list.item(r, 1),
                                                                                     type(None)) or isinstance(
                    self.table_list.item(r, 2), type(None))):
                current = float(self.table_list.item(r, 0).text())
                highest_current = current if current > highest_current else highest_current
                current_slope = float(self.table_list.item(r, 1).text())
                duration = float(self.table_list.item(r, 2).text())
                steps.append(ListStep(current, current_slope, duration))
            elif self.table_row_incomplete(r):
                QMessageBox.warning(self, "Incomplete entry", "Incomplete entry on row " + (r + 1).__str__())
                return

        try:
            LoadList(self.val_list_slot.value(), highest_current, steps, self.val_list_loops.value()).validate()
        except Exception as ex:
            self.display_error(ex)
        else:
            QMessageBox.information(self, "Validation OK", "Validation passed")

    def recall_list(self):
        self.table_list.clearContents()
        try:
            self.wait_and_set_block()
            load_list: LoadList = load.get_list(self.val_list_slot.value())
            self.unset_block()
        except (serial.serialutil.PortNotOpenError, ValueError) as ex:
            self.unset_block()
            self.display_error(ex)
            return

        steps = load_list.steps
        for step in range(len(steps)):
            item = QTableWidgetItem(QtCore.Qt.ItemDataRole.EditRole)
            item2 = QTableWidgetItem(QtCore.Qt.ItemDataRole.EditRole)
            item3 = QTableWidgetItem(QtCore.Qt.ItemDataRole.EditRole)
            item.setData(QtCore.Qt.ItemDataRole.EditRole, steps[step].current)
            item2.setData(QtCore.Qt.ItemDataRole.EditRole, steps[step].current_slope)
            item3.setData(QtCore.Qt.ItemDataRole.EditRole, steps[step].duration)
            self.table_list.setItem(step, 0, item)
            self.table_list.setItem(step, 1, item2)
            self.table_list.setItem(step, 2, item3)
        self.val_list_loops.setValue(load_list.loop_number)

    def update_list(self, list_u: LoadList):
        self.table_list.clearContents()
        try:
            load_list: LoadList = list_u
        except (serial.serialutil.PortNotOpenError, ValueError) as ex:
            self.display_error(ex)
            return

        steps = load_list.steps
        for step in range(len(steps)):
            item = QTableWidgetItem(QtCore.Qt.ItemDataRole.EditRole)
            item2 = QTableWidgetItem(QtCore.Qt.ItemDataRole.EditRole)
            item3 = QTableWidgetItem(QtCore.Qt.ItemDataRole.EditRole)
            item.setData(QtCore.Qt.ItemDataRole.EditRole, steps[step].current)
            item2.setData(QtCore.Qt.ItemDataRole.EditRole, steps[step].current_slope)
            item3.setData(QtCore.Qt.ItemDataRole.EditRole, steps[step].duration)
            self.table_list.setItem(step, 0, item)
            self.table_list.setItem(step, 1, item2)
            self.table_list.setItem(step, 2, item3)
        self.val_list_loops.setValue(load_list.loop_number)

    def clear_marked_list(self):
        selection = self.table_list.selectedItems()
        while selection.__len__() > 0:
            self.table_list.takeItem(selection[selection.__len__() - 1].row(), selection.pop().column())

    def init_saves(self):
        confirmation_box = QMessageBox.warning(self.parent(), "Confirm Initialization",
                                               "Confirm Initialization of all save slots?\n\n" + "This will set values in all available save slots(memory, lists,...) and OVERWRITE all saved values.\n\nThis might take a while!",
                                               QMessageBox.StandardButton.Cancel | QMessageBox.StandardButton.Ok,
                                               QMessageBox.StandardButton.Cancel)
        try:
            if confirmation_box == QMessageBox.StandardButton.Ok:
                self.groupBox_settings.setTitle("Device Settings - Initializing Saves")
                QtCore.QCoreApplication.processEvents()
                for i in range(1, 11):
                    load.set_batt(BattList(i, 1, 1, 1, 1, 1), False)
                    load.set_ocp(OCPList(i, 5, 1, 1, 1, 0.1, 0.1, 0.1, 1, 0.3, 0.2), False)
                    load.set_opp(OPPList(i, 5, 1, 1, 1, 0.1, 0.1, 0.1, 2, 0.3, 0.2), False)
                    if i < 8:
                        load.set_list(LoadList(i, 2, [ListStep(1, 0.1, 1), ListStep(2, 0.2, 2)], 3), False)
                self.groupBox_settings.setTitle("Device Settings")

        except serial.serialutil.PortNotOpenError as ex:
            self.display_error(ex)
            self.groupBox_settings.setTitle("Device Settings")
            return


def main():
    app = QtWidgets.QApplication(sys.argv)
    window = MainWindow()
    window.show()
    atexit.register(exit_handler, window)
    sys.exit(app.exec())


# python bit to figure how who started This
if __name__ == "__main__":
    main()
