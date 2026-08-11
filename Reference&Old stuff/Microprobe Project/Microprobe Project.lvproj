<?xml version='1.0' encoding='UTF-8'?>
<Project Type="Project" LVVersion="13008000">
	<Property Name="NI.LV.All.SourceOnly" Type="Bool">false</Property>
	<Item Name="My Computer" Type="My Computer">
		<Property Name="IOScan.Faults" Type="Str"></Property>
		<Property Name="IOScan.NetVarPeriod" Type="UInt">100</Property>
		<Property Name="IOScan.NetWatchdogEnabled" Type="Bool">false</Property>
		<Property Name="IOScan.Period" Type="UInt">10000</Property>
		<Property Name="IOScan.PowerupMode" Type="UInt">0</Property>
		<Property Name="IOScan.Priority" Type="UInt">9</Property>
		<Property Name="IOScan.ReportModeConflict" Type="Bool">true</Property>
		<Property Name="IOScan.StartEngineOnDeploy" Type="Bool">false</Property>
		<Property Name="NI.SortType" Type="Int">3</Property>
		<Property Name="server.app.propertiesEnabled" Type="Bool">true</Property>
		<Property Name="server.control.propertiesEnabled" Type="Bool">true</Property>
		<Property Name="server.tcp.enabled" Type="Bool">false</Property>
		<Property Name="server.tcp.port" Type="Int">0</Property>
		<Property Name="server.tcp.serviceName" Type="Str">My Computer/VI Server</Property>
		<Property Name="server.tcp.serviceName.default" Type="Str">My Computer/VI Server</Property>
		<Property Name="server.vi.callsEnabled" Type="Bool">true</Property>
		<Property Name="server.vi.propertiesEnabled" Type="Bool">true</Property>
		<Property Name="specify.custom.address" Type="Bool">false</Property>
		<Item Name="Project Documentation" Type="Folder">
			<Item Name="Documentation Images" Type="Folder">
				<Item Name="loc_open_data_typedef.png" Type="Document" URL="../documentation/loc_open_data_typedef.png"/>
				<Item Name="loc_open_states_typedef.png" Type="Document" URL="../documentation/loc_open_states_typedef.png"/>
				<Item Name="loc_simple_state_machine.png" Type="Document" URL="../documentation/loc_simple_state_machine.png"/>
				<Item Name="loc_state_transition.png" Type="Document" URL="../documentation/loc_state_transition.png"/>
				<Item Name="loc_transition_error.png" Type="Document" URL="../documentation/loc_transition_error.png"/>
				<Item Name="loc_use_state_data.png" Type="Document" URL="../documentation/loc_use_state_data.png"/>
				<Item Name="loc_conditional_state_transition.png" Type="Document" URL="../documentation/loc_conditional_state_transition.png"/>
				<Item Name="loc_new_button.png" Type="Document" URL="../documentation/loc_new_button.png"/>
				<Item Name="loc_new_button_transition.png" Type="Document" URL="../documentation/loc_new_button_transition.png"/>
				<Item Name="loc_new_button_value_change.png" Type="Document" URL="../documentation/loc_new_button_value_change.png"/>
				<Item Name="loc_new_state.png" Type="Document" URL="../documentation/loc_new_state.png"/>
			</Item>
			<Item Name="Simple State Machine Documentation.html" Type="Document" URL="../documentation/Simple State Machine Documentation.html"/>
		</Item>
		<Item Name="Type Definitions" Type="Folder">
			<Item Name="Data.ctl" Type="VI" URL="../controls/Data.ctl"/>
			<Item Name="State.ctl" Type="VI" URL="../controls/State.ctl"/>
			<Item Name="Microprobe Vars.ctl" Type="VI" URL="../controls/Microprobe Vars.ctl"/>
			<Item Name="ProgramState.ctl" Type="VI" URL="../controls/ProgramState.ctl"/>
			<Item Name="ProgramColumn.ctl" Type="VI" URL="../controls/ProgramColumn.ctl"/>
		</Item>
		<Item Name="Main.vi" Type="VI" URL="../Main.vi"/>
		<Item Name="Main2.vi" Type="VI" URL="../Main2.vi"/>
		<Item Name="Write to probe file.vi" Type="VI" URL="../Write to probe file.vi"/>
		<Item Name="Initialize_Motor.vi" Type="VI" URL="/&lt;instrlib&gt;/Microprobe/Motor/Initialize_Motor.vi"/>
		<Item Name="Initialize_Watlow.vi" Type="VI" URL="/&lt;instrlib&gt;/Watlow EZ-ZONE/Public/Initialize_Watlow.vi"/>
		<Item Name="MoveToPosition.vi" Type="VI" URL="/&lt;instrlib&gt;/IMS stepper motors/MoveToPosition.vi"/>
		<Item Name="ReadCurrentPosition.vi" Type="VI" URL="/&lt;instrlib&gt;/IMS stepper motors/ReadCurrentPosition.vi"/>
		<Item Name="WaitUntilInPosition.vi" Type="VI" URL="/&lt;instrlib&gt;/IMS stepper motors/WaitUntilInPosition.vi"/>
		<Item Name="JogToPosition.vi" Type="VI" URL="/&lt;instrlib&gt;/IMS stepper motors/JogToPosition.vi"/>
		<Item Name="ProjectState.ctl" Type="VI" URL="../controls/ProjectState.ctl"/>
		<Item Name="AOUpdate.vi" Type="VI" URL="/&lt;vilib&gt;/addons/LabJack/ljackuw.llb/AOUpdate.vi"/>
		<Item Name="UpdateStatus.vi" Type="VI" URL="../UpdateStatus.vi"/>
		<Item Name="CreateScanFilename.vi" Type="VI" URL="../CreateScanFilename.vi"/>
		<Item Name="ChangePosition.vi" Type="VI" URL="../ChangePosition.vi"/>
		<Item Name="WriteToLogFile.vi" Type="VI" URL="/&lt;instrlib&gt;/Microprobe/WriteToLogFile.vi"/>
		<Item Name="ReadProgramData.vi" Type="VI" URL="../ReadProgramData.vi"/>
		<Item Name="IsProbeTouchingDot" Type="VI" URL="../IsProbeTouchingDot"/>
		<Item Name="MakeScanFilename.vi" Type="VI" URL="../MakeScanFilename.vi"/>
		<Item Name="CalculatePO2.vi" Type="VI" URL="../CalculatePO2.vi"/>
		<Item Name="SquareNyquistAxes.vi" Type="VI" URL="/&lt;userlib&gt;/Plotting/SquareNyquistAxes.vi"/>
		<Item Name="SweepParameters.ctl" Type="VI" URL="/&lt;userlib&gt;/SweepParameters.ctl"/>
		<Item Name="peis_CK.vi" Type="VI" URL="../../../../../../EC-Lab Development Package/Examples/Labview/V12/peis_CK.vi"/>
		<Item Name="PastImpedanceTimeConstant.vi" Type="VI" URL="/&lt;userlib&gt;/PastImpedanceTimeConstant.vi"/>
		<Item Name="Dependencies" Type="Dependencies">
			<Item Name="vi.lib" Type="Folder">
				<Item Name="Simple Error Handler.vi" Type="VI" URL="/&lt;vilib&gt;/Utility/error.llb/Simple Error Handler.vi"/>
				<Item Name="DialogType.ctl" Type="VI" URL="/&lt;vilib&gt;/Utility/error.llb/DialogType.ctl"/>
				<Item Name="General Error Handler.vi" Type="VI" URL="/&lt;vilib&gt;/Utility/error.llb/General Error Handler.vi"/>
				<Item Name="DialogTypeEnum.ctl" Type="VI" URL="/&lt;vilib&gt;/Utility/error.llb/DialogTypeEnum.ctl"/>
				<Item Name="General Error Handler CORE.vi" Type="VI" URL="/&lt;vilib&gt;/Utility/error.llb/General Error Handler CORE.vi"/>
				<Item Name="whitespace.ctl" Type="VI" URL="/&lt;vilib&gt;/Utility/error.llb/whitespace.ctl"/>
				<Item Name="Check Special Tags.vi" Type="VI" URL="/&lt;vilib&gt;/Utility/error.llb/Check Special Tags.vi"/>
				<Item Name="TagReturnType.ctl" Type="VI" URL="/&lt;vilib&gt;/Utility/error.llb/TagReturnType.ctl"/>
				<Item Name="Set String Value.vi" Type="VI" URL="/&lt;vilib&gt;/Utility/error.llb/Set String Value.vi"/>
				<Item Name="GetRTHostConnectedProp.vi" Type="VI" URL="/&lt;vilib&gt;/Utility/error.llb/GetRTHostConnectedProp.vi"/>
				<Item Name="Error Code Database.vi" Type="VI" URL="/&lt;vilib&gt;/Utility/error.llb/Error Code Database.vi"/>
				<Item Name="Trim Whitespace.vi" Type="VI" URL="/&lt;vilib&gt;/Utility/error.llb/Trim Whitespace.vi"/>
				<Item Name="Format Message String.vi" Type="VI" URL="/&lt;vilib&gt;/Utility/error.llb/Format Message String.vi"/>
				<Item Name="Find Tag.vi" Type="VI" URL="/&lt;vilib&gt;/Utility/error.llb/Find Tag.vi"/>
				<Item Name="Search and Replace Pattern.vi" Type="VI" URL="/&lt;vilib&gt;/Utility/error.llb/Search and Replace Pattern.vi"/>
				<Item Name="Set Bold Text.vi" Type="VI" URL="/&lt;vilib&gt;/Utility/error.llb/Set Bold Text.vi"/>
				<Item Name="Details Display Dialog.vi" Type="VI" URL="/&lt;vilib&gt;/Utility/error.llb/Details Display Dialog.vi"/>
				<Item Name="ErrWarn.ctl" Type="VI" URL="/&lt;vilib&gt;/Utility/error.llb/ErrWarn.ctl"/>
				<Item Name="eventvkey.ctl" Type="VI" URL="/&lt;vilib&gt;/event_ctls.llb/eventvkey.ctl"/>
				<Item Name="Clear Errors.vi" Type="VI" URL="/&lt;vilib&gt;/Utility/error.llb/Clear Errors.vi"/>
				<Item Name="Not Found Dialog.vi" Type="VI" URL="/&lt;vilib&gt;/Utility/error.llb/Not Found Dialog.vi"/>
				<Item Name="Three Button Dialog.vi" Type="VI" URL="/&lt;vilib&gt;/Utility/error.llb/Three Button Dialog.vi"/>
				<Item Name="Three Button Dialog CORE.vi" Type="VI" URL="/&lt;vilib&gt;/Utility/error.llb/Three Button Dialog CORE.vi"/>
				<Item Name="Longest Line Length in Pixels.vi" Type="VI" URL="/&lt;vilib&gt;/Utility/error.llb/Longest Line Length in Pixels.vi"/>
				<Item Name="Convert property node font to graphics font.vi" Type="VI" URL="/&lt;vilib&gt;/Utility/error.llb/Convert property node font to graphics font.vi"/>
				<Item Name="Get Text Rect.vi" Type="VI" URL="/&lt;vilib&gt;/picture/picture.llb/Get Text Rect.vi"/>
				<Item Name="Get String Text Bounds.vi" Type="VI" URL="/&lt;vilib&gt;/Utility/error.llb/Get String Text Bounds.vi"/>
				<Item Name="LVBoundsTypeDef.ctl" Type="VI" URL="/&lt;vilib&gt;/Utility/miscctls.llb/LVBoundsTypeDef.ctl"/>
				<Item Name="BuildHelpPath.vi" Type="VI" URL="/&lt;vilib&gt;/Utility/error.llb/BuildHelpPath.vi"/>
				<Item Name="GetHelpDir.vi" Type="VI" URL="/&lt;vilib&gt;/Utility/error.llb/GetHelpDir.vi"/>
				<Item Name="Space Constant.vi" Type="VI" URL="/&lt;vilib&gt;/dlg_ctls.llb/Space Constant.vi"/>
				<Item Name="VISA Configure Serial Port (Instr).vi" Type="VI" URL="/&lt;vilib&gt;/Instr/_visa.llb/VISA Configure Serial Port (Instr).vi"/>
				<Item Name="VISA Configure Serial Port (Serial Instr).vi" Type="VI" URL="/&lt;vilib&gt;/Instr/_visa.llb/VISA Configure Serial Port (Serial Instr).vi"/>
				<Item Name="VISA Configure Serial Port" Type="VI" URL="/&lt;vilib&gt;/Instr/_visa.llb/VISA Configure Serial Port"/>
				<Item Name="VISA Set IO Buffer Mask.ctl" Type="VI" URL="/&lt;vilib&gt;/Instr/_visa.llb/VISA Set IO Buffer Mask.ctl"/>
				<Item Name="NI_PackedLibraryUtility.lvlib" Type="Library" URL="/&lt;vilib&gt;/Utility/LVLibp/NI_PackedLibraryUtility.lvlib"/>
				<Item Name="Error Cluster From Error Code.vi" Type="VI" URL="/&lt;vilib&gt;/Utility/error.llb/Error Cluster From Error Code.vi"/>
				<Item Name="NI_FileType.lvlib" Type="Library" URL="/&lt;vilib&gt;/Utility/lvfile.llb/NI_FileType.lvlib"/>
				<Item Name="Check if File or Folder Exists.vi" Type="VI" URL="/&lt;vilib&gt;/Utility/libraryn.llb/Check if File or Folder Exists.vi"/>
				<Item Name="Find First Error.vi" Type="VI" URL="/&lt;vilib&gt;/Utility/error.llb/Find First Error.vi"/>
				<Item Name="Close File+.vi" Type="VI" URL="/&lt;vilib&gt;/Utility/file.llb/Close File+.vi"/>
				<Item Name="compatReadText.vi" Type="VI" URL="/&lt;vilib&gt;/_oldvers/_oldvers.llb/compatReadText.vi"/>
				<Item Name="Read File+ (string).vi" Type="VI" URL="/&lt;vilib&gt;/Utility/file.llb/Read File+ (string).vi"/>
				<Item Name="Open File+.vi" Type="VI" URL="/&lt;vilib&gt;/Utility/file.llb/Open File+.vi"/>
				<Item Name="Read Lines From File.vi" Type="VI" URL="/&lt;vilib&gt;/Utility/file.llb/Read Lines From File.vi"/>
				<Item Name="Read From Spreadsheet File (string).vi" Type="VI" URL="/&lt;vilib&gt;/Utility/file.llb/Read From Spreadsheet File (string).vi"/>
				<Item Name="Read From Spreadsheet File (I64).vi" Type="VI" URL="/&lt;vilib&gt;/Utility/file.llb/Read From Spreadsheet File (I64).vi"/>
				<Item Name="Read From Spreadsheet File (DBL).vi" Type="VI" URL="/&lt;vilib&gt;/Utility/file.llb/Read From Spreadsheet File (DBL).vi"/>
				<Item Name="Read From Spreadsheet File.vi" Type="VI" URL="/&lt;vilib&gt;/Utility/file.llb/Read From Spreadsheet File.vi"/>
				<Item Name="EAnalogOut.vi" Type="VI" URL="/&lt;vilib&gt;/addons/LabJack/ljackuw.llb/EAnalogOut.vi"/>
				<Item Name="VoltsToBits.vi" Type="VI" URL="/&lt;vilib&gt;/addons/LabJack/ljackuw.llb/VoltsToBits.vi"/>
				<Item Name="clean array.vi" Type="VI" URL="/&lt;vilib&gt;/addons/LabJack/ljackuw.llb/clean array.vi"/>
				<Item Name="AISample.vi" Type="VI" URL="/&lt;vilib&gt;/addons/LabJack/ljackuw.llb/AISample.vi"/>
				<Item Name="Write Spreadsheet String.vi" Type="VI" URL="/&lt;vilib&gt;/Utility/file.llb/Write Spreadsheet String.vi"/>
				<Item Name="Write To Spreadsheet File (DBL).vi" Type="VI" URL="/&lt;vilib&gt;/Utility/file.llb/Write To Spreadsheet File (DBL).vi"/>
				<Item Name="Write To Spreadsheet File (string).vi" Type="VI" URL="/&lt;vilib&gt;/Utility/file.llb/Write To Spreadsheet File (string).vi"/>
				<Item Name="Write To Spreadsheet File (I64).vi" Type="VI" URL="/&lt;vilib&gt;/Utility/file.llb/Write To Spreadsheet File (I64).vi"/>
				<Item Name="Write To Spreadsheet File.vi" Type="VI" URL="/&lt;vilib&gt;/Utility/file.llb/Write To Spreadsheet File.vi"/>
				<Item Name="Close Panel No Abort.vi" Type="VI" URL="/&lt;vilib&gt;/Utility/victl.llb/Close Panel No Abort.vi"/>
				<Item Name="viRef buffer.vi" Type="VI" URL="/&lt;vilib&gt;/Utility/victl.llb/viRef buffer.vi"/>
				<Item Name="Beep.vi" Type="VI" URL="/&lt;vilib&gt;/Platform/system.llb/Beep.vi"/>
				<Item Name="FindElementStartByName.vi" Type="VI" URL="/&lt;vilib&gt;/Utility/xml.llb/FindElementStartByName.vi"/>
				<Item Name="FindCloseTagByName.vi" Type="VI" URL="/&lt;vilib&gt;/Utility/xml.llb/FindCloseTagByName.vi"/>
				<Item Name="FindMatchingCloseTag.vi" Type="VI" URL="/&lt;vilib&gt;/Utility/xml.llb/FindMatchingCloseTag.vi"/>
				<Item Name="FindElement.vi" Type="VI" URL="/&lt;vilib&gt;/Utility/xml.llb/FindElement.vi"/>
				<Item Name="FindEmptyElement.vi" Type="VI" URL="/&lt;vilib&gt;/Utility/xml.llb/FindEmptyElement.vi"/>
				<Item Name="FindFirstTag.vi" Type="VI" URL="/&lt;vilib&gt;/Utility/xml.llb/FindFirstTag.vi"/>
				<Item Name="ParseXMLFragments.vi" Type="VI" URL="/&lt;vilib&gt;/Utility/xml.llb/ParseXMLFragments.vi"/>
			</Item>
			<Item Name="instr.lib" Type="Folder">
				<Item Name="Address.ctl" Type="VI" URL="/&lt;instrlib&gt;/IMS stepper motors/Address.ctl"/>
				<Item Name="Set Parameter.ctl" Type="VI" URL="/&lt;instrlib&gt;/IMS stepper motors/Set Parameter.ctl"/>
				<Item Name="PartyMode_MDrive_Set_Parameter.vi" Type="VI" URL="/&lt;instrlib&gt;/IMS stepper motors/PartyMode_MDrive_Set_Parameter.vi"/>
				<Item Name="Truncate_after_third_decimal_place.vi" Type="VI" URL="/&lt;instrlib&gt;/Microprobe/Truncate_after_third_decimal_place.vi"/>
				<Item Name="Get Parameter.ctl" Type="VI" URL="/&lt;instrlib&gt;/IMS stepper motors/Get Parameter.ctl"/>
				<Item Name="PartyMode_MDrive_Get_Parameter_Windows7.vi" Type="VI" URL="/&lt;instrlib&gt;/IMS stepper motors/PartyMode_MDrive_Get_Parameter_Windows7.vi"/>
				<Item Name="EZZone_Write.vi" Type="VI" URL="/&lt;instrlib&gt;/Watlow EZ-ZONE/Public/EZZone_Write.vi"/>
				<Item Name="Convert °C to °F.vi" Type="VI" URL="/&lt;instrlib&gt;/Watlow EZ-ZONE/Public/Convert °C to °F.vi"/>
				<Item Name="Watlow_Set_Parameter.ctl" Type="VI" URL="/&lt;instrlib&gt;/Watlow EZ-ZONE/Public/Watlow_Set_Parameter.ctl"/>
				<Item Name="Set Parameter.vi" Type="VI" URL="/&lt;instrlib&gt;/Watlow EZ-ZONE/Public/Set Parameter.vi"/>
				<Item Name="EZZone_Initialize.vi" Type="VI" URL="/&lt;instrlib&gt;/Watlow EZ-ZONE/Public/EZZone_Initialize.vi"/>
				<Item Name="MDrive_Set_Parameter.vi" Type="VI" URL="/&lt;instrlib&gt;/IMS stepper motors/MDrive_Set_Parameter.vi"/>
				<Item Name="MDrive_Init_Com.vi" Type="VI" URL="/&lt;instrlib&gt;/IMS stepper motors/MDrive_Init_Com.vi"/>
				<Item Name="Initialize_and_Turn_On_PartyMode.vi" Type="VI" URL="/&lt;instrlib&gt;/IMS stepper motors/Initialize_and_Turn_On_PartyMode.vi"/>
				<Item Name="InitializeMotorPositions.vi" Type="VI" URL="/&lt;instrlib&gt;/Microprobe/InitializeMotorPositions.vi"/>
				<Item Name="EZZONEDriver.dll" Type="Document" URL="/&lt;instrlib&gt;/Watlow EZ-ZONE/Public/EZZONEDriver.dll"/>
				<Item Name="Convert °F to °C.vi" Type="VI" URL="/&lt;instrlib&gt;/Watlow EZ-ZONE/Public/Convert °F to °C.vi"/>
				<Item Name="EZZone_Read.vi" Type="VI" URL="/&lt;instrlib&gt;/Watlow EZ-ZONE/Public/EZZone_Read.vi"/>
				<Item Name="Watlow_Get_Parameter.ctl" Type="VI" URL="/&lt;instrlib&gt;/Watlow EZ-ZONE/Public/Watlow_Get_Parameter.ctl"/>
				<Item Name="Get Parameter.vi" Type="VI" URL="/&lt;instrlib&gt;/Watlow EZ-ZONE/Public/Get Parameter.vi"/>
				<Item Name="readMFC_revised.vi" Type="VI" URL="/&lt;instrlib&gt;/Aera MFCs/Aera_MFC.llb/readMFC_revised.vi"/>
				<Item Name="Read_Watlow.vi" Type="VI" URL="/&lt;instrlib&gt;/Watlow EZ-ZONE/Public/Read_Watlow.vi"/>
				<Item Name="setMFC_revisedc.vi" Type="VI" URL="/&lt;instrlib&gt;/Aera MFCs/Aera_MFC.llb/setMFC_revisedc.vi"/>
				<Item Name="MDrive_Close_Com.vi" Type="VI" URL="/&lt;instrlib&gt;/IMS stepper motors/MDrive_Close_Com.vi"/>
				<Item Name="EZZone_Close.vi" Type="VI" URL="/&lt;instrlib&gt;/Watlow EZ-ZONE/Public/EZZone_Close.vi"/>
				<Item Name="Close.vi" Type="VI" URL="/&lt;instrlib&gt;/Watlow EZ-ZONE(R) LabView Driver/Public/Close.vi"/>
				<Item Name="Agilent 428X Series.lvlib" Type="Library" URL="/&lt;instrlib&gt;/Agilent 428X Series/Agilent 428X Series.lvlib"/>
				<Item Name="Frequency sweep.vi" Type="VI" URL="/&lt;instrlib&gt;/Custom/Frequency sweep.vi"/>
				<Item Name="Agilent 4285A Series Frequency Sweep.vi" Type="VI" URL="/&lt;instrlib&gt;/Agilent 428X Series/Examples/Agilent 4285A Series Frequency Sweep.vi"/>
				<Item Name="Agilent 428X Series Measure Capacitor D Value.vi" Type="VI" URL="/&lt;instrlib&gt;/Agilent 428X Series/Examples/Agilent 428X Series Measure Capacitor D Value.vi"/>
				<Item Name="Solartron 1260.lvlib" Type="Library" URL="/&lt;instrlib&gt;/Solartron 1260/Solartron 1260.lvlib"/>
				<Item Name="S1260RecycleSweep.vi" Type="VI" URL="/&lt;instrlib&gt;/Solartron 1260/S1260RecycleSweep.vi"/>
			</Item>
			<Item Name="ljackuw.dll" Type="Document" URL="ljackuw.dll">
				<Property Name="NI.PreserveRelativePath" Type="Bool">true</Property>
			</Item>
			<Item Name="CreateSampleFilePaths.vi" Type="VI" URL="../CreateSampleFilePaths.vi"/>
			<Item Name="InitializeComms.vi" Type="VI" URL="../InitializeComms.vi"/>
			<Item Name="Type Descriptor Enumeration__ogtk.ctl" Type="VI" URL="../../../../../../EC-Lab Development Package/Examples/Labview/V12/base_xml.llb/Type Descriptor Enumeration__ogtk.ctl"/>
			<Item Name="connect.vi" Type="VI" URL="../../../../../../EC-Lab Development Package/Examples/Labview/V12/dll_Functions.llb/connect.vi"/>
			<Item Name="path.vi" Type="VI" URL="../../../../../../EC-Lab Development Package/Examples/Labview/V12/dll_Functions.llb/path.vi"/>
			<Item Name="GetChannelplugged.vi" Type="VI" URL="../../../../../../EC-Lab Development Package/Examples/Labview/V12/dll_Functions.llb/GetChannelplugged.vi"/>
			<Item Name="LoadFirmware.vi" Type="VI" URL="../../../../../../EC-Lab Development Package/Examples/Labview/V12/dll_Functions.llb/LoadFirmware.vi"/>
			<Item Name="disconnect.vi" Type="VI" URL="../../../../../../EC-Lab Development Package/Examples/Labview/V12/dll_Functions.llb/disconnect.vi"/>
			<Item Name="GetChannelInfo.vi" Type="VI" URL="../../../../../../EC-Lab Development Package/Examples/Labview/V12/dll_Functions.llb/GetChannelInfo.vi"/>
			<Item Name="max_bdwidth.vi" Type="VI" URL="../../../../../../EC-Lab Development Package/Examples/Labview/V12/dll_Functions.llb/max_bdwidth.vi"/>
			<Item Name="max_min_irange.vi" Type="VI" URL="../../../../../../EC-Lab Development Package/Examples/Labview/V12/dll_Functions.llb/max_min_irange.vi"/>
			<Item Name="OpenG Flatten to XML.vi" Type="VI" URL="../../../../../../EC-Lab Development Package/Examples/Labview/V12/base_xml.llb/OpenG Flatten to XML.vi"/>
			<Item Name="Get TDEnum from Data__ogtk.vi" Type="VI" URL="../../../../../../EC-Lab Development Package/Examples/Labview/V12/base_xml.llb/Get TDEnum from Data__ogtk.vi"/>
			<Item Name="Get Header from TD__ogtk.vi" Type="VI" URL="../../../../../../EC-Lab Development Package/Examples/Labview/V12/base_xml.llb/Get Header from TD__ogtk.vi"/>
			<Item Name="Type Descriptor__ogtk.ctl" Type="VI" URL="../../../../../../EC-Lab Development Package/Examples/Labview/V12/base_xml.llb/Type Descriptor__ogtk.ctl"/>
			<Item Name="Type Descriptor Header__ogtk.ctl" Type="VI" URL="../../../../../../EC-Lab Development Package/Examples/Labview/V12/base_xml.llb/Type Descriptor Header__ogtk.ctl"/>
			<Item Name="Date Type Format String Mapping.vi" Type="VI" URL="../../../../../../EC-Lab Development Package/Examples/Labview/V12/base_xml.llb/Date Type Format String Mapping.vi"/>
			<Item Name="Build Error Cluster__ogtk.vi" Type="VI" URL="../../../../../../EC-Lab Development Package/Examples/Labview/V12/base_xml.llb/Build Error Cluster__ogtk.vi"/>
			<Item Name="1D Array to String__ogtk.vi" Type="VI" URL="../../../../../../EC-Lab Development Package/Examples/Labview/V12/base_xml.llb/1D Array to String__ogtk.vi"/>
			<Item Name="Format XML Header.vi" Type="VI" URL="../../../../../../EC-Lab Development Package/Examples/Labview/V12/base_xml.llb/Format XML Header.vi"/>
			<Item Name="Get Strings from Enum__ogtk.vi" Type="VI" URL="../../../../../../EC-Lab Development Package/Examples/Labview/V12/base_xml.llb/Get Strings from Enum__ogtk.vi"/>
			<Item Name="Variant to Header Info__ogtk.vi" Type="VI" URL="../../../../../../EC-Lab Development Package/Examples/Labview/V12/base_xml.llb/Variant to Header Info__ogtk.vi"/>
			<Item Name="Get Strings from Enum TD__ogtk.vi" Type="VI" URL="../../../../../../EC-Lab Development Package/Examples/Labview/V12/base_xml.llb/Get Strings from Enum TD__ogtk.vi"/>
			<Item Name="Get PString__ogtk.vi" Type="VI" URL="../../../../../../EC-Lab Development Package/Examples/Labview/V12/base_xml.llb/Get PString__ogtk.vi"/>
			<Item Name="Array Size(s)__ogtk.vi" Type="VI" URL="../../../../../../EC-Lab Development Package/Examples/Labview/V12/base_xml.llb/Array Size(s)__ogtk.vi"/>
			<Item Name="No of Elements in Cluster__ogtk.vi" Type="VI" URL="../../../../../../EC-Lab Development Package/Examples/Labview/V12/base_xml.llb/No of Elements in Cluster__ogtk.vi"/>
			<Item Name="Get Data Type XML String.vi" Type="VI" URL="../../../../../../EC-Lab Development Package/Examples/Labview/V12/base_xml.llb/Get Data Type XML String.vi"/>
			<Item Name="Get Data Name__ogtk.vi" Type="VI" URL="../../../../../../EC-Lab Development Package/Examples/Labview/V12/base_xml.llb/Get Data Name__ogtk.vi"/>
			<Item Name="Get Data Name from TD__ogtk.vi" Type="VI" URL="../../../../../../EC-Lab Development Package/Examples/Labview/V12/base_xml.llb/Get Data Name from TD__ogtk.vi"/>
			<Item Name="Get Last PString__ogtk.vi" Type="VI" URL="../../../../../../EC-Lab Development Package/Examples/Labview/V12/base_xml.llb/Get Last PString__ogtk.vi"/>
			<Item Name="Format Generic Data to XML Value.vi" Type="VI" URL="../../../../../../EC-Lab Development Package/Examples/Labview/V12/base_xml.llb/Format Generic Data to XML Value.vi"/>
			<Item Name="Format Variant Into String__ogtk.vi" Type="VI" URL="../../../../../../EC-Lab Development Package/Examples/Labview/V12/base_xml.llb/Format Variant Into String__ogtk.vi"/>
			<Item Name="Strip Units__ogtk.vi" Type="VI" URL="../../../../../../EC-Lab Development Package/Examples/Labview/V12/base_xml.llb/Strip Units__ogtk.vi"/>
			<Item Name="Get Array Element TDEnum__ogtk.vi" Type="VI" URL="../../../../../../EC-Lab Development Package/Examples/Labview/V12/base_xml.llb/Get Array Element TDEnum__ogtk.vi"/>
			<Item Name="Cluster to Array of VData__ogtk.vi" Type="VI" URL="../../../../../../EC-Lab Development Package/Examples/Labview/V12/base_xml.llb/Cluster to Array of VData__ogtk.vi"/>
			<Item Name="Split Cluster TD__ogtk.vi" Type="VI" URL="../../../../../../EC-Lab Development Package/Examples/Labview/V12/base_xml.llb/Split Cluster TD__ogtk.vi"/>
			<Item Name="Parse String with TDs__ogtk.vi" Type="VI" URL="../../../../../../EC-Lab Development Package/Examples/Labview/V12/base_xml.llb/Parse String with TDs__ogtk.vi"/>
			<Item Name="Array to Array of VData__ogtk.vi" Type="VI" URL="../../../../../../EC-Lab Development Package/Examples/Labview/V12/base_xml.llb/Array to Array of VData__ogtk.vi"/>
			<Item Name="Reshape Array to 1D VArray__ogtk.vi" Type="VI" URL="../../../../../../EC-Lab Development Package/Examples/Labview/V12/base_xml.llb/Reshape Array to 1D VArray__ogtk.vi"/>
			<Item Name="Set Data Name__ogtk.vi" Type="VI" URL="../../../../../../EC-Lab Development Package/Examples/Labview/V12/base_xml.llb/Set Data Name__ogtk.vi"/>
			<Item Name="Get Default Data from TD__ogtk.vi" Type="VI" URL="../../../../../../EC-Lab Development Package/Examples/Labview/V12/base_xml.llb/Get Default Data from TD__ogtk.vi"/>
			<Item Name="Array of VData to VCluster__ogtk.vi" Type="VI" URL="../../../../../../EC-Lab Development Package/Examples/Labview/V12/base_xml.llb/Array of VData to VCluster__ogtk.vi"/>
			<Item Name="Get Array Element TD__ogtk.vi" Type="VI" URL="../../../../../../EC-Lab Development Package/Examples/Labview/V12/base_xml.llb/Get Array Element TD__ogtk.vi"/>
			<Item Name="Get Element TD from Array TD__ogtk.vi" Type="VI" URL="../../../../../../EC-Lab Development Package/Examples/Labview/V12/base_xml.llb/Get Element TD from Array TD__ogtk.vi"/>
			<Item Name="parse_protocol.vi" Type="VI" URL="../../../../../../EC-Lab Development Package/Examples/Labview/V12/dll_Functions.llb/parse_protocol.vi"/>
			<Item Name="parse_params.vi" Type="VI" URL="../../../../../../EC-Lab Development Package/Examples/Labview/V12/dll_Functions.llb/parse_params.vi"/>
			<Item Name="LoadTechnique.vi" Type="VI" URL="../../../../../../EC-Lab Development Package/Examples/Labview/V12/dll_Functions.llb/LoadTechnique.vi"/>
			<Item Name="StartChannel.vi" Type="VI" URL="../../../../../../EC-Lab Development Package/Examples/Labview/V12/dll_Functions.llb/StartChannel.vi"/>
			<Item Name="GetData_CK.vi" Type="VI" URL="../../../../../../EC-Lab Development Package/Examples/Labview/V12/dll_Functions.llb/GetData_CK.vi"/>
			<Item Name="StopChannel.vi" Type="VI" URL="../../../../../../EC-Lab Development Package/Examples/Labview/V12/dll_Functions.llb/StopChannel.vi"/>
		</Item>
		<Item Name="Build Specifications" Type="Build">
			<Item Name="Main Application" Type="EXE">
				<Property Name="App_copyErrors" Type="Bool">true</Property>
				<Property Name="App_INI_aliasGUID" Type="Str">{B511CD8F-BE21-4470-A7C4-3ACBADFC72C8}</Property>
				<Property Name="App_INI_GUID" Type="Str">{78EABFCD-5B97-4F87-AF3F-FF129EE73305}</Property>
				<Property Name="App_serverConfig.httpPort" Type="Int">8002</Property>
				<Property Name="Bld_buildCacheID" Type="Str">{2CE40C4E-446A-4350-9E11-C85D8BAE8F13}</Property>
				<Property Name="Bld_buildSpecName" Type="Str">Main Application</Property>
				<Property Name="Bld_excludeLibraryItems" Type="Bool">true</Property>
				<Property Name="Bld_excludePolymorphicVIs" Type="Bool">true</Property>
				<Property Name="Bld_localDestDir" Type="Path">../builds/NI_AB_PROJECTNAME/Main Application</Property>
				<Property Name="Bld_localDestDirType" Type="Str">relativeToCommon</Property>
				<Property Name="Bld_modifyLibraryFile" Type="Bool">true</Property>
				<Property Name="Bld_previewCacheID" Type="Str">{09FD23A0-93D2-46A2-B12D-FA02255F97C6}</Property>
				<Property Name="Bld_version.major" Type="Int">1</Property>
				<Property Name="Destination[0].destName" Type="Str">Main.exe</Property>
				<Property Name="Destination[0].path" Type="Path">../builds/NI_AB_PROJECTNAME/Main Application/Main.exe</Property>
				<Property Name="Destination[0].preserveHierarchy" Type="Bool">true</Property>
				<Property Name="Destination[0].type" Type="Str">App</Property>
				<Property Name="Destination[1].destName" Type="Str">Support Directory</Property>
				<Property Name="Destination[1].path" Type="Path">../builds/NI_AB_PROJECTNAME/Main Application/data</Property>
				<Property Name="DestinationCount" Type="Int">2</Property>
				<Property Name="Source[0].itemID" Type="Str">{E14DB2DD-E011-49B1-8A6D-0145D8676319}</Property>
				<Property Name="Source[0].type" Type="Str">Container</Property>
				<Property Name="Source[1].destinationIndex" Type="Int">0</Property>
				<Property Name="Source[1].itemID" Type="Ref">/My Computer/Main.vi</Property>
				<Property Name="Source[1].sourceInclusion" Type="Str">TopLevel</Property>
				<Property Name="Source[1].type" Type="Str">VI</Property>
				<Property Name="SourceCount" Type="Int">2</Property>
				<Property Name="TgtF_fileDescription" Type="Str">Main Application</Property>
				<Property Name="TgtF_internalName" Type="Str">Main Application</Property>
				<Property Name="TgtF_legalCopyright" Type="Str">Copyright © 2012 </Property>
				<Property Name="TgtF_productName" Type="Str">Main Application</Property>
				<Property Name="TgtF_targetfileGUID" Type="Str">{A9B9C488-ADD7-40F6-AAC2-547427E5CB24}</Property>
				<Property Name="TgtF_targetfileName" Type="Str">Main.exe</Property>
			</Item>
		</Item>
	</Item>
</Project>
